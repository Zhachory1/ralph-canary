from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from flag_service.domain import FlagError
from flag_service.app import create_app
from flag_service.cache import RedisFlagCache
from flag_service.models import Flag, FlagHistory
from flag_service.service import FlagService

from conftest import TOKENS, auth


def test_create_evaluate_history_and_actor_attribution(setup_app, request_id):
    client, _, _, _ = setup_app
    created = client.post(
        "/v1/admin/flags",
        headers={**auth(), "X-Request-ID": request_id},
        json={"flag_key": "checkout.new_flow", "value": True},
    )
    assert created.status_code == 201
    assert created.headers["etag"] == '"1"'
    assert created.json()["version"] == "1"

    evaluated = client.get("/v1/flags/checkout.new_flow", headers=auth("reader"))
    assert evaluated.status_code == 200
    assert evaluated.json()["value"] is True
    assert evaluated.json()["version"] == "1"

    history = client.get("/v1/admin/flags/checkout.new_flow/history", headers=auth("auditor"))
    assert history.status_code == 200
    assert history.json()["items"] == [{
        "flag_key": "checkout.new_flow",
        "value": True,
        "version": "1",
        "actor_id": "operator-test",
        "request_id": request_id,
        "operation": "create",
        "created_at": history.json()["items"][0]["created_at"],
    }]


def test_strict_validation_authorization_and_error_envelopes(setup_app):
    client, _, _, _ = setup_app
    assert client.get("/v1/flags/unknown", headers=auth("reader")).json()["code"] == "FLAG_NOT_FOUND"
    assert client.get("/v1/admin/flags/x/history", headers=auth("reader")).status_code == 403
    assert client.post("/v1/admin/flags", json={"flag_key": "abc", "value": True}).status_code == 401

    for key in ("Upper", "a/b", "a\n", "é", "a" * 129):
        response = client.post("/v1/admin/flags", headers=auth(), json={"flag_key": key, "value": True})
        assert response.status_code == 422
        assert set(response.json()) == {"code", "message", "request_id"}

    for value in ("true", 1, None):
        response = client.post("/v1/admin/flags", headers=auth(), json={"flag_key": "safe.key", "value": value})
        assert response.status_code == 422
    assert client.post("/v1/admin/flags", headers=auth(), json={"flag_key": "safe.key", "value": True, "extra": 1}).status_code == 422
    too_large = client.post("/v1/admin/flags", headers=auth(), content=b"x" * 16_385)
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "REQUEST_TOO_LARGE"


def test_duplicate_preconditions_same_value_and_failed_write_history(setup_app):
    client, _, _, _ = setup_app
    assert client.post("/v1/admin/flags", headers=auth(), json={"flag_key": "rollout", "value": False}).status_code == 201
    duplicate = client.post("/v1/admin/flags", headers=auth(), json={"flag_key": "rollout", "value": True})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "FLAG_ALREADY_EXISTS"

    missing = client.put("/v1/admin/flags/rollout", headers=auth(), json={"value": True})
    assert missing.status_code == 428
    stale = client.put("/v1/admin/flags/rollout", headers={**auth(), "If-Match": '"9"'}, json={"value": True})
    assert stale.status_code == 412
    assert stale.json()["current_version"] == "1"
    noncanonical = client.put("/v1/admin/flags/rollout", headers={**auth(), "If-Match": '"01"'}, json={"value": True})
    assert noncanonical.status_code == 400

    update = client.put("/v1/admin/flags/rollout", headers={**auth(), "If-Match": '"1"'}, json={"value": False})
    assert update.status_code == 200
    assert update.headers["etag"] == '"2"'
    history = client.get("/v1/admin/flags/rollout/history", headers=auth("auditor"))
    assert [item["version"] for item in history.json()["items"]] == ["2", "1"]
    assert history.json()["items"][0]["value"] is False
    assert history.json()["items"][0]["operation"] == "update"


def test_list_cursor_order_and_page_size_bounds(setup_app):
    client, _, _, _ = setup_app
    for key in ("gamma", "alpha", "beta"):
        response = client.post("/v1/admin/flags", headers=auth(), json={"flag_key": key, "value": True})
        assert response.status_code == 201
    first = client.get("/v1/admin/flags?limit=1", headers=auth("reader")).json()
    second = client.get(f"/v1/admin/flags?limit=1&cursor={first['next_cursor']}", headers=auth("reader")).json()
    assert first["items"][0]["flag_key"] == "alpha"
    assert second["items"][0]["flag_key"] == "beta"
    assert client.get("/v1/admin/flags?limit=201", headers=auth("reader")).status_code == 422
    assert client.get("/v1/admin/flags?cursor=!!!!", headers=auth("reader")).status_code == 422


def test_bigint_exhaustion_and_no_history_mutation(setup_app):
    client, engine, _, _ = setup_app
    client.post("/v1/admin/flags", headers=auth(), json={"flag_key": "exhausted", "value": True})
    with engine.begin() as connection:
        connection.execute(text("UPDATE flags SET current_version = 9223372036854775807 WHERE flag_key = 'exhausted'"))
    before = client.get("/v1/admin/flags/exhausted/history", headers=auth("auditor")).json()["items"]
    result = client.put(
        "/v1/admin/flags/exhausted",
        headers={**auth(), "If-Match": '"9223372036854775807"'},
        json={"value": False},
    )
    assert result.status_code == 409
    assert result.json()["code"] == "VERSION_EXHAUSTED"
    after = client.get("/v1/admin/flags/exhausted/history", headers=auth("auditor")).json()["items"]
    assert after == before


def test_operator_write_rate_limit_is_enforced(setup_app):
    _, engine, _, cache = setup_app
    app = create_app(engine=engine, cache_client=cache, auth_tokens=TOKENS, eviction_submit=lambda function, *args: function(*args), write_limit=1)
    with TestClient(app) as client:
        first = client.post("/v1/admin/flags", headers=auth(), json={"flag_key": "limit.first", "value": True})
        second = client.post("/v1/admin/flags", headers=auth(), json={"flag_key": "limit.second", "value": True})
    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"


def test_atomic_rollback_when_history_insert_fails(setup_app):
    client, engine, _, _ = setup_app
    with engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER fail_history BEFORE INSERT ON flag_history BEGIN SELECT RAISE(ABORT, 'injected'); END"))
    response = client.post("/v1/admin/flags", headers=auth(), json={"flag_key": "atomic", "value": True})
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert client.get("/v1/flags/atomic", headers=auth("reader")).status_code == 404


def test_concurrent_updates_with_same_version_have_one_winner(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'concurrent.db'}", connect_args={"timeout": 10})
    from flag_service.models import Base
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    service = FlagService(sessions, RedisFlagCache(None))
    service.create("concurrent", False, "operator-test", uuid4())
    barrier = Barrier(2)

    def attempt(value):
        barrier.wait()
        try:
            return service.update("concurrent", value, "1", "operator-test", uuid4()).version
        except FlagError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [True, False]))
    assert outcomes.count("2") == 1
    assert outcomes.count("VERSION_MISMATCH") == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(FlagHistory)) == 2
    engine.dispose()
