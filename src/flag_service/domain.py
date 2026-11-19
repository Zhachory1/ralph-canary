from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

KEY_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,127}", re.ASCII)
MAX_VERSION = 9_223_372_036_854_775_807
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class FlagError(Exception):
    def __init__(self, status: int, code: str, message: str, current_version: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.current_version = current_version


@dataclass(frozen=True)
class FlagRecord:
    flag_key: str
    value: bool
    version: str
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "flag_key": self.flag_key,
            "value": self.value,
            "version": self.version,
            "updated_at": format_utc(self.updated_at),
        }


def validate_key(key: str) -> str:
    if not isinstance(key, str) or KEY_PATTERN.fullmatch(key) is None:
        raise FlagError(422, "INVALID_FLAG_KEY", "flag_key must match ^[a-z][a-z0-9_.-]{0,127}$")
    return key


def validate_page_size(value: int | None) -> int:
    size = DEFAULT_PAGE_SIZE if value is None else value
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_PAGE_SIZE:
        raise FlagError(422, "INVALID_PAGE_SIZE", "limit must be between 1 and 200")
    return size


def format_utc(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise FlagError(422, "INVALID_CURSOR", "cursor is invalid") from None
    if not decoded or len(decoded) > 128:
        raise FlagError(422, "INVALID_CURSOR", "cursor is invalid")
    return decoded
