from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .auth import Principal, load_tokens, make_auth
from .cache import RedisFlagCache
from .domain import FlagError
from .service import FlagService

logger = logging.getLogger("flag_service.audit")
MAX_BODY_BYTES = 16_384
WRITE_LIMIT_WINDOW_SECONDS = 60


class WriteRateLimiter:
    def __init__(self, limit: int, window_seconds: int = WRITE_LIMIT_WINDOW_SECONDS):
        if limit < 1:
            raise ValueError("write limit must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.events = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, actor_id: str) -> bool:
        now = time.monotonic()
        with self.lock:
            events = self.events[actor_id]
            while events and events[0] <= now - self.window_seconds:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


class RequestBodyLimitMiddleware:
    def __init__(self, app, maximum: int):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-request-id":
                try:
                    request_id = str(UUID(value.decode("ascii")))
                except (UnicodeDecodeError, ValueError):
                    pass
        request_id = request_id or str(uuid4())
        content_length = next((value for name, value in scope.get("headers", []) if name.lower() == b"content-length"), b"")
        if content_length.isdigit() and int(content_length) > self.maximum:
            response = JSONResponse(
                {"code": "REQUEST_TOO_LARGE", "message": "Request body exceeds the size limit", "request_id": request_id},
                status_code=413,
                headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        chunks = []
        size = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.maximum:
                response = JSONResponse(
                    {"code": "REQUEST_TOO_LARGE", "message": "Request body exceeds the size limit", "request_id": request_id},
                    status_code=413,
                    headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            more_body = message.get("more_body", False)

        body = b"".join(chunks)
        body_sent = False

        async def replay_body():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_body, send)


class WriteFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: StrictBool


class CreateFlag(WriteFlag):
    flag_key: str


def create_app(database_url: str | None = None, cache_client=None, engine=None, auth_tokens=None, eviction_submit=None, write_limit: int | None = None) -> FastAPI:
    app = FastAPI(title="ralph-canary Boolean Flag Evaluator", docs_url=None, redoc_url=None)
    app.add_middleware(RequestBodyLimitMiddleware, maximum=MAX_BODY_BYTES)
    db_engine = engine or create_engine(
        database_url or os.getenv("DATABASE_URL", "postgresql+psycopg://flag_app:local-only@localhost:5432/flags"),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3} if (database_url or os.getenv("DATABASE_URL", "")).startswith("postgresql") else {},
    )
    app.state.engine = db_engine
    app.state.sessions = sessionmaker(bind=db_engine, expire_on_commit=False, class_=Session)
    app.state.write_limiter = WriteRateLimiter(write_limit if write_limit is not None else int(os.getenv("FLAG_WRITE_LIMIT_PER_MINUTE", "60")))
    if cache_client is None:
        redis_url = os.getenv("REDIS_URL", "")
        if redis_url:
            cache_client = Redis.from_url(redis_url, socket_connect_timeout=0.5, socket_timeout=0.5, decode_responses=True)
    app.state.flag_service = FlagService(app.state.sessions, RedisFlagCache(cache_client, eviction_submit))
    tokens = load_tokens() if auth_tokens is None else auth_tokens
    require = make_auth(tokens)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        try:
            request_id = UUID(request.headers.get("X-Request-ID", ""))
        except (ValueError, TypeError):
            request_id = uuid4()
        request.state.request_id = request_id
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdecimal() and int(content_length) > MAX_BODY_BYTES:
            response = JSONResponse(
                {"code": "REQUEST_TOO_LARGE", "message": "Request body exceeds the size limit", "request_id": str(request_id)},
                status_code=413,
            )
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = str(request_id)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(FlagError)
    async def flag_error_handler(request: Request, exc: FlagError):
        payload = {"code": exc.code, "message": exc.message, "request_id": str(request.state.request_id)}
        if exc.current_version is not None:
            payload["current_version"] = exc.current_version
        return JSONResponse(payload, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {"code": "VALIDATION_ERROR", "message": "Request validation failed", "request_id": str(request.state.request_id)},
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        if exc.status_code == 401:
            code, message = "AUTHENTICATION_REQUIRED", "Authentication required"
        elif exc.status_code == 403:
            code, message = "FORBIDDEN", "Not authorized"
        else:
            code, message = "HTTP_ERROR", "Request could not be completed"
        headers = exc.headers or {}
        return JSONResponse(
            {"code": code, "message": message, "request_id": str(request.state.request_id)},
            status_code=exc.status_code,
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        return JSONResponse(
            {"code": "SERVICE_UNAVAILABLE", "message": "The flag service is temporarily unavailable", "request_id": str(request.state.request_id)},
            status_code=503,
        )

    def service(request: Request) -> FlagService:
        return request.app.state.flag_service

    def audit(principal: Principal, request: Request, action: str) -> None:
        logger.info("admin_action=%s actor_id=%s request_id=%s", action, principal.actor_id, request.state.request_id)

    def limit_write(request: Request, principal: Principal) -> None:
        if not request.app.state.write_limiter.allow(principal.actor_id):
            raise FlagError(429, "RATE_LIMITED", "Write rate limit exceeded; try again later")

    @app.get("/v1/flags/{flag_key}")
    def evaluate(flag_key: str, request: Request, principal: Principal = Depends(require("read"))):
        return service(request).get(flag_key).as_dict()

    @app.get("/v1/admin/flags")
    def list_flags(
        request: Request,
        limit: int | None = Query(default=50, ge=1, le=200),
        cursor: str | None = None,
        principal: Principal = Depends(require("read")),
    ):
        audit(principal, request, "list_flags")
        items, next_cursor = service(request).list_flags(limit, cursor)
        return {"items": [item.as_dict() for item in items], "next_cursor": next_cursor}

    @app.post("/v1/admin/flags", status_code=201)
    def create_flag(body: CreateFlag, request: Request, response: Response, principal: Principal = Depends(require("write"))):
        limit_write(request, principal)
        record = service(request).create(body.flag_key, body.value, principal.actor_id, request.state.request_id)
        response.headers["ETag"] = f'"{record.version}"'
        audit(principal, request, "create_flag")
        return record.as_dict()

    @app.put("/v1/admin/flags/{flag_key}")
    def update_flag(
        flag_key: str,
        body: WriteFlag,
        request: Request,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
        principal: Principal = Depends(require("write")),
    ):
        limit_write(request, principal)
        expected = if_match
        if expected is not None:
            if len(expected) < 3 or expected[0] != '"' or expected[-1] != '"':
                raise FlagError(400, "INVALID_PRECONDITION", "If-Match must be a quoted version")
            expected = expected[1:-1]
        record = service(request).update(flag_key, body.value, expected, principal.actor_id, request.state.request_id)
        response.headers["ETag"] = f'"{record.version}"'
        audit(principal, request, "update_flag")
        return record.as_dict()

    @app.get("/v1/admin/flags/{flag_key}/history")
    def flag_history(
        flag_key: str,
        request: Request,
        limit: int | None = Query(default=50, ge=1, le=200),
        cursor: str | None = None,
        principal: Principal = Depends(require("audit")),
    ):
        audit(principal, request, "read_history")
        items, next_cursor = service(request).history(flag_key, limit, cursor)
        return {"items": items, "next_cursor": next_cursor}

    @app.get("/admin", include_in_schema=False)
    def admin_panel():
        return FileResponse(Path(__file__).with_name("admin.html"))

    @app.get("/admin.js", include_in_schema=False)
    def admin_script():
        return FileResponse(Path(__file__).with_name("admin.js"), media_type="text/javascript")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
