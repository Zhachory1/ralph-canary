from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, desc, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Flag(Base):
    __tablename__ = "flags"
    __table_args__ = (CheckConstraint("current_version >= 1", name="ck_flags_positive_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    flag_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    current_value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    current_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class FlagHistory(Base):
    __tablename__ = "flag_history"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_flag_history_positive_version"),
        CheckConstraint("operation IN ('create','update')", name="ck_flag_history_operation"),
        UniqueConstraint("flag_id", "version", name="uq_flag_history_flag_version"),
        Index("ix_flag_history_flag_version", "flag_id", desc("version")),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    flag_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("flags.id"), nullable=False)
    flag_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
