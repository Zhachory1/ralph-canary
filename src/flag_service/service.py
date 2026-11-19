from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .cache import RedisFlagCache
from .domain import FlagError, FlagRecord, MAX_VERSION, decode_cursor, encode_cursor, format_utc, validate_key, validate_page_size
from .models import Flag, FlagHistory


class FlagService:
    def __init__(self, sessions: sessionmaker[Session], cache: RedisFlagCache):
        self.sessions = sessions
        self.cache = cache

    @staticmethod
    def _record(row: Flag) -> FlagRecord:
        return FlagRecord(row.flag_key, row.current_value, str(row.current_version), row.updated_at)

    @staticmethod
    def _backend_error() -> FlagError:
        return FlagError(503, "SERVICE_UNAVAILABLE", "The flag service is temporarily unavailable")

    def get(self, flag_key: str) -> FlagRecord:
        validate_key(flag_key)
        cached = self.cache.get(flag_key)
        if cached is not None:
            return cached
        try:
            with self.sessions() as session:
                row = session.scalar(select(Flag).where(Flag.flag_key == flag_key))
                if row is None:
                    raise FlagError(404, "FLAG_NOT_FOUND", "Flag not found")
                record = self._record(row)
        except FlagError:
            raise
        except SQLAlchemyError:
            raise self._backend_error() from None
        self.cache.populate(record)
        return record

    def create(self, flag_key: str, value: bool, actor_id: str, request_id: UUID) -> FlagRecord:
        validate_key(flag_key)
        if type(value) is not bool:
            raise FlagError(422, "INVALID_VALUE", "value must be a JSON boolean")
        flag_id = uuid4()
        try:
            with self.sessions.begin() as session:
                row = Flag(id=flag_id, flag_key=flag_key, current_value=value, current_version=1)
                session.add(row)
                session.flush()
                session.add(FlagHistory(
                    id=uuid4(), flag_id=flag_id, flag_key=flag_key, value=value, version=1,
                    actor_id=actor_id, request_id=request_id, operation="create",
                ))
                session.flush()
                record = self._record(row)
        except IntegrityError:
            try:
                with self.sessions() as session:
                    exists = session.scalar(select(Flag.id).where(Flag.flag_key == flag_key)) is not None
            except SQLAlchemyError:
                raise self._backend_error() from None
            if exists:
                raise FlagError(409, "FLAG_ALREADY_EXISTS", "Flag already exists") from None
            raise self._backend_error() from None
        except SQLAlchemyError:
            raise self._backend_error() from None
        self.cache.evict(flag_key)
        return record

    def update(self, flag_key: str, value: bool, expected_version: str | None, actor_id: str, request_id: UUID) -> FlagRecord:
        validate_key(flag_key)
        if type(value) is not bool:
            raise FlagError(422, "INVALID_VALUE", "value must be a JSON boolean")
        if expected_version is None:
            raise FlagError(428, "PRECONDITION_REQUIRED", "If-Match is required")
        if (
            not expected_version.isascii()
            or not expected_version.isdecimal()
            or len(expected_version) > 19
            or not 1 <= int(expected_version) <= MAX_VERSION
            or str(int(expected_version)) != expected_version
        ):
            raise FlagError(400, "INVALID_PRECONDITION", "If-Match must contain a positive decimal version")
        current_expected = int(expected_version)
        try:
            with self.sessions.begin() as session:
                statement = (
                    update(Flag)
                    .where(
                        Flag.flag_key == flag_key,
                        Flag.current_version == current_expected,
                        Flag.current_version < MAX_VERSION,
                    )
                    .values(current_value=value, current_version=Flag.current_version + 1)
                    .values(updated_at=func.now())
                    .returning(Flag)
                )
                row = session.scalars(statement).one_or_none()
                if row is None:
                    current = session.scalar(select(Flag).where(Flag.flag_key == flag_key))
                    if current is None:
                        raise FlagError(404, "FLAG_NOT_FOUND", "Flag not found")
                    if current.current_version == MAX_VERSION:
                        raise FlagError(409, "VERSION_EXHAUSTED", "Flag version limit has been reached")
                    raise FlagError(412, "VERSION_MISMATCH", "Flag version does not match", str(current.current_version))
                session.add(FlagHistory(
                    id=uuid4(), flag_id=row.id, flag_key=row.flag_key, value=value,
                    version=row.current_version, actor_id=actor_id, request_id=request_id, operation="update",
                ))
                session.flush()
                record = self._record(row)
        except FlagError:
            raise
        except SQLAlchemyError:
            raise self._backend_error() from None
        self.cache.evict(flag_key)
        return record

    def list_flags(self, limit: int | None, cursor: str | None) -> tuple[list[FlagRecord], str | None]:
        size = validate_page_size(limit)
        after = decode_cursor(cursor)
        try:
            with self.sessions() as session:
                query = select(Flag).order_by(Flag.flag_key).limit(size + 1)
                if after:
                    query = query.where(Flag.flag_key > after)
                rows = list(session.scalars(query))
        except SQLAlchemyError:
            raise self._backend_error() from None
        has_more = len(rows) > size
        records = [self._record(row) for row in rows[:size]]
        next_cursor = None
        if has_more and records:
            next_cursor = encode_cursor(records[-1].flag_key)
        return records, next_cursor

    def history(self, flag_key: str, limit: int | None, cursor: str | None) -> tuple[list[dict], str | None]:
        validate_key(flag_key)
        size = validate_page_size(limit)
        after = decode_cursor(cursor)
        if after is not None and (
            not after.isascii()
            or not after.isdecimal()
            or len(after) > 19
            or not 1 <= int(after) <= MAX_VERSION
            or str(int(after)) != after
        ):
            raise FlagError(422, "INVALID_CURSOR", "cursor is invalid")
        try:
            with self.sessions() as session:
                flag = session.scalar(select(Flag).where(Flag.flag_key == flag_key))
                if flag is None:
                    raise FlagError(404, "FLAG_NOT_FOUND", "Flag not found")
                query = select(FlagHistory).where(FlagHistory.flag_id == flag.id).order_by(FlagHistory.version.desc()).limit(size + 1)
                if after is not None:
                    query = query.where(FlagHistory.version < int(after))
                rows = list(session.scalars(query))
        except FlagError:
            raise
        except SQLAlchemyError:
            raise self._backend_error() from None
        has_more = len(rows) > size
        page = rows[:size]
        items = [{
            "flag_key": row.flag_key,
            "value": row.value,
            "version": str(row.version),
            "actor_id": row.actor_id,
            "request_id": str(row.request_id),
            "operation": row.operation,
            "created_at": format_utc(row.created_at),
        } for row in page]
        next_cursor = encode_cursor(str(page[-1].version)) if has_more and page else None
        return items, next_cursor
