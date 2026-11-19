from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from flag_service.app import create_app
from flag_service.models import Base


TOKENS = [
    {"token": "reader-token-for-test-0001", "actor_id": "reader-test", "role": "reader"},
    {"token": "auditor-token-for-test-0002", "actor_id": "auditor-test", "role": "auditor"},
    {"token": "operator-token-for-test-0003", "actor_id": "operator-test", "role": "operator"},
]


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}
        self.set_calls = []
        self.deleted = []
        self.now = 0
        self.fail_get = False
        self.fail_set = False
        self.fail_delete = False

    def get(self, key):
        if self.fail_get:
            raise OSError("redis unavailable")
        if self.expirations.get(key, 0) <= self.now:
            self.values.pop(key, None)
            return None
        return self.values.get(key)

    def setex(self, key, seconds, value):
        if self.fail_set:
            raise OSError("redis unavailable")
        self.set_calls.append((key, seconds, value))
        self.values[key] = value
        self.expirations[key] = self.now + seconds

    def delete(self, key):
        if self.fail_delete:
            raise OSError("redis unavailable")
        self.deleted.append(key)
        self.values.pop(key, None)
        self.expirations.pop(key, None)


@pytest.fixture
def setup_app():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    cache = MemoryRedis()
    app = create_app(engine=engine, cache_client=cache, auth_tokens=TOKENS, eviction_submit=lambda function, *args: function(*args))
    with TestClient(app) as client:
        yield client, engine, sessions, cache
    engine.dispose()


@pytest.fixture
def request_id():
    return str(uuid4())


def auth(role="operator"):
    token = next(item["token"] for item in TOKENS if item["role"] == role)
    return {"Authorization": f"Bearer {token}"}
