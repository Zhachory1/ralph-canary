import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import update

from flag_service.cache import CACHE_TTL_SECONDS, RedisFlagCache
from flag_service.domain import FlagError, FlagRecord
from flag_service.models import Flag
from flag_service.service import FlagService


def test_cache_fixed_ttl_hit_expiry_and_stale_repopulation_bound(setup_app):
    client, engine, sessions, cache = setup_app
    service = client.app.state.flag_service
    service.create("cache.flag", True, "operator-test", uuid4())
    service.get("cache.flag")
    assert cache.set_calls[-1][1] == 300
    key, expiration = cache.set_calls[-1][0], cache.expirations["ralph-canary:flag:v1:cache.flag"]
    cache.now += 200
    assert service.get("cache.flag").version == "1"
    assert cache.expirations[key] == expiration
    with engine.begin() as connection:
        connection.execute(update(Flag).where(Flag.flag_key == "cache.flag").values(current_value=False, current_version=2))
    assert service.get("cache.flag").value is True
    cache.now = expiration
    current = service.get("cache.flag")
    assert current.version == "2"
    assert cache.set_calls[-1][1] == CACHE_TTL_SECONDS
    assert cache.expirations[key] == cache.now + CACHE_TTL_SECONDS


def test_unusable_cache_and_redis_failures_fall_back_and_eviction_is_best_effort(setup_app):
    client, _, _, cache = setup_app
    service = client.app.state.flag_service
    service.create("fallback", True, "operator-test", uuid4())
    cache.values["ralph-canary:flag:v1:fallback"] = "not-json"
    cache.expirations["ralph-canary:flag:v1:fallback"] = 1000
    assert service.get("fallback").value is True
    cache.fail_get = True
    cache.fail_set = True
    cache.fail_delete = True
    updated = service.update("fallback", False, "1", "operator-test", uuid4())
    assert updated.version == "2"
    assert service.get("fallback").value is False


def test_redis_unavailable_reads_and_writes_use_postgresql(setup_app):
    client, _, _, cache = setup_app
    service = client.app.state.flag_service
    service.create("redis.outage", True, "operator-test", uuid4())

    cache.fail_get = True
    cache.fail_set = True
    cache.fail_delete = True

    assert service.get("redis.outage").value is True
    updated = service.update("redis.outage", False, "1", "operator-test", uuid4())
    assert updated.version == "2"
    assert service.get("redis.outage").value is False


def test_corrupt_valid_json_is_not_treated_as_a_flag(setup_app):
    client, _, _, cache = setup_app
    service = client.app.state.flag_service
    service.create("corrupt", True, "operator-test", uuid4())
    cache.values["ralph-canary:flag:v1:corrupt"] = json.dumps({"flag_key": "corrupt", "value": "true", "version": "1", "updated_at": "bad"})
    cache.expirations["ralph-canary:flag:v1:corrupt"] = 100
    assert service.get("corrupt").value is True


def test_postgresql_outage_uses_cache_hit_but_returns_503_on_miss():
    class BrokenSession:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def scalar(self, *args, **kwargs):
            from sqlalchemy.exc import OperationalError
            raise OperationalError("select", {}, OSError("offline"))

    sessions = lambda: BrokenSession()

    class CacheClient:
        def __init__(self, payload=None):
            self.payload = payload
        def get(self, key):
            return self.payload
        def setex(self, *args):
            pass
        def delete(self, *args):
            pass

    live = FlagRecord("offline", True, "7", datetime.now(timezone.utc)).as_dict()
    cached_service = FlagService(sessions, RedisFlagCache(CacheClient(json.dumps(live))))
    assert cached_service.get("offline").version == "7"
    unavailable = FlagService(sessions, RedisFlagCache(CacheClient()))
    try:
        unavailable.get("offline")
    except FlagError as error:
        assert error.status == 503
        assert error.code == "SERVICE_UNAVAILABLE"
    else:
        raise AssertionError("database miss should fail closed")


def test_post_commit_eviction_is_dispatched_without_waiting(setup_app):
    client, _, sessions, cache = setup_app
    queued = []
    service = FlagService(sessions, RedisFlagCache(cache, lambda function, *args: queued.append((function, args))))
    service.create("async.evict", True, "operator-test", uuid4())
    create_evict, args = queued.pop()
    create_evict(*args)
    service.get("async.evict")

    updated = service.update("async.evict", False, "1", "operator-test", uuid4())
    assert updated.version == "2"
    assert service.get("async.evict").value is True
    assert len(queued) == 1
    update_evict, args = queued.pop()
    update_evict(*args)
    assert service.get("async.evict").value is False
