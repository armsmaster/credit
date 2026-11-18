import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, MetaData, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(schema="curves")


class Currency(Base):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    numeric_code: Mapped[str | None] = mapped_column(String(3))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CurveDefinition(Base):
    __tablename__ = "curve_definitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    currency_code: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    index_name: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    day_count: Mapped[str] = mapped_column(Text, default="ACT/365F")
    fixed_frequency_months: Mapped[int] = mapped_column(Integer, default=12)
    single_payment_max_months: Mapped[int] = mapped_column(Integer, default=12)
    default_params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class _CurveSnapshot:
    """Columns shared by the current state and history versions."""

    as_of_date: Mapped[date] = mapped_column(Date)
    input_mode: Mapped[str] = mapped_column(Text)
    quotes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    conventions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    comment: Mapped[str] = mapped_column(Text, default="")


class CurveState(_CurveSnapshot, Base):
    """Current (intraday) curve: one row per curve, overwritten on save."""

    __tablename__ = "curve_states"

    curve_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curve_definitions.id"), primary_key=True)
    saved_by: Mapped[str] = mapped_column(Text)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CurveHistory(_CurveSnapshot, Base):
    """Immutable historical versions; a change of history adds a new version."""

    __tablename__ = "curve_history"
    __table_args__ = (UniqueConstraint("curve_id", "as_of_date", "version", name="uq_curve_history_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    curve_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curve_definitions.id"))
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)  # snapshot | auto_archive | manual_edit
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
