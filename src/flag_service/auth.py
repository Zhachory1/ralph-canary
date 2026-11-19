from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from typing import Iterable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role: str


CAPABILITIES = {
    "reader": frozenset({"read"}),
    "auditor": frozenset({"read", "audit"}),
    "operator": frozenset({"read", "write"}),
}


def load_tokens(raw: str | None = None) -> list[dict[str, str]]:
    value = os.getenv("FLAG_AUTH_TOKENS", "") if raw is None else raw
    if not value:
        return []
    try:
        entries = json.loads(value)
        if not isinstance(entries, list):
            return []
        valid = []
        seen_tokens = set()
        for entry in entries:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("token"), str)
                and len(entry["token"]) >= 16
                and entry["token"].isascii()
                and entry["token"].isprintable()
                and isinstance(entry.get("actor_id"), str)
                and entry["actor_id"]
                and len(entry["actor_id"]) <= 256
                and all(char.isprintable() for char in entry["actor_id"])
                and entry.get("role") in CAPABILITIES
                and entry["token"] not in seen_tokens
            ):
                valid.append({"token": entry["token"], "actor_id": entry["actor_id"], "role": entry["role"]})
                seen_tokens.add(entry["token"])
            else:
                return []
        return valid
    except (ValueError, TypeError):
        return []


def make_auth(tokens: Iterable[dict[str, str]]):
    bearer = HTTPBearer(auto_error=False)
    configured = list(tokens)

    def require(capability: str):
        async def dependency(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
            if credentials is None or credentials.scheme.lower() != "bearer":
                raise HTTPException(status_code=401, detail="authentication required", headers={"WWW-Authenticate": "Bearer"})
            principal = None
            for entry in configured:
                if hmac.compare_digest(credentials.credentials, entry["token"]):
                    principal = Principal(entry["actor_id"], entry["role"])
            if principal is None:
                raise HTTPException(status_code=401, detail="authentication required", headers={"WWW-Authenticate": "Bearer"})
            if capability not in CAPABILITIES[principal.role]:
                raise HTTPException(status_code=403, detail="not authorized")
            return principal
        return dependency
    return require
