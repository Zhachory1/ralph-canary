from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Protocol

from .domain import FlagRecord

logger = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 300
CACHE_KEY_PREFIX = "ralph-canary:flag:v1:"
_eviction_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="flag-cache-evict")


class Cache(Protocol):
    def get(self, key: str) -> str | bytes | None: ...
    def setex(self, key: str, seconds: int, value: str) -> object: ...
    def delete(self, key: str) -> object: ...


class RedisFlagCache:
    def __init__(self, client: Cache | None, eviction_submit: Callable | None = None):
        self.client = client
        self.eviction_submit = eviction_submit or _eviction_executor.submit

    @staticmethod
    def key(flag_key: str) -> str:
        return CACHE_KEY_PREFIX + flag_key

    def get(self, flag_key: str) -> FlagRecord | None:
        if self.client is None:
            return None
        try:
            payload = self.client.get(self.key(flag_key))
            if payload is None:
                return None
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
            version = data["version"]
            if (
                data["flag_key"] != flag_key
                or type(data["value"]) is not bool
                or not isinstance(version, str)
                or not version.isascii()
                or not version.isdecimal()
                or not 1 <= int(version) <= 9_223_372_036_854_775_807
                or str(int(version)) != version
            ):
                return None
            updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
            return FlagRecord(flag_key, data["value"], version, updated_at)
        except Exception:
            logger.info("Flag cache read unavailable or unusable")
            return None

    def populate(self, record: FlagRecord) -> None:
        if self.client is None:
            return
        try:
            self.client.setex(self.key(record.flag_key), CACHE_TTL_SECONDS, json.dumps(record.as_dict()))
        except Exception:
            logger.info("Flag cache population failed")

    def evict(self, flag_key: str) -> None:
        if self.client is None:
            return
        try:
            self.eviction_submit(self._delete, self.key(flag_key))
        except Exception:
            logger.info("Flag cache eviction could not be scheduled")

    def _delete(self, key: str) -> None:
        try:
            self.client.delete(key)
        except Exception:
            logger.info("Flag cache eviction failed")
