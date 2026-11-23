import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(schema="credit")


# ------------------------------------------------------------------ reference data (ISS)
class Issuer(Base):
    """Issuer tracked by the service (ISS emitter). Its bonds are scanned daily."""

    __tablename__ = "issuers"

    emitter_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(Text)
    short_title: Mapped[str] = mapped_column(Text, default="")
    inn: Mapped[str | None] = mapped_column(Text)
    ogrn: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    added_by: Mapped[str] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scan_status: Mapped[str | None] = mapped_column(Text)  # queued | running | ok | error
    scan_error: Mapped[str | None] = mapped_column(Text)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Bond(Base):
    __tablename__ = "bonds"

    secid: Mapped[str] = mapped_column(Text, primary_key=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.emitter_id"), index=True)
    isin: Mapped[str | None] = mapped_column(Text)
    shortname: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(Text, default="")
    board: Mapped[str | None] = mapped_column(Text)
    is_traded: Mapped[bool] = mapped_column(Boolean, default=True)
    face_unit: Mapped[str | None] = mapped_column(Text)
    face_value: Mapped[float | None] = mapped_column(Float)
    mat_date: Mapped[date | None] = mapped_column(Date)
    desc: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # ISS description (selected fields)
    schedule: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # ISS bondization
    manual: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # operator overrides
    manual_updated_by: Mapped[str | None] = mapped_column(Text)
    manual_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    liquidity: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    liquidity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history_until: Mapped[date | None] = mapped_column(Date)  # last ISS history date loaded
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BondDaily(Base):
    """One row per bond and trading day: ISS history + our end-of-day (19:00) market snapshot."""

    __tablename__ = "bond_daily"

    secid: Mapped[str] = mapped_column(ForeignKey("bonds.secid", ondelete="CASCADE"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    # ISS history
    face_value: Mapped[float | None] = mapped_column(Float)
    accrued: Mapped[float | None] = mapped_column(Float)
    num_trades: Mapped[int | None] = mapped_column(Integer)
    turnover: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    waprice: Mapped[float | None] = mapped_column(Float)
    # end of the main session (from intraday snapshots and candles)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float)
    vwap: Mapped[float | None] = mapped_column(Float)
    dema: Mapped[float | None] = mapped_column(Float)
    dema_half_life: Mapped[float | None] = mapped_column(Float)
    eod_face_value: Mapped[float | None] = mapped_column(Float)
    eod_accrued: Mapped[float | None] = mapped_column(Float)
    eod_settle_date: Mapped[date | None] = mapped_column(Date)
    eod_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    spread_pct: Mapped[float | None] = mapped_column(Float)  # median intraday bid-ask spread, % of mid
    snapshots: Mapped[int | None] = mapped_column(Integer)


class BondSnapshot(Base):
    """Intraday order-book / trade snapshot every 15 minutes (kept for the current day only)."""

    __tablename__ = "bond_snapshots"

    secid: Mapped[str] = mapped_column(ForeignKey("bonds.secid", ondelete="CASCADE"), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    market_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # ISS data time (delayed)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float)
    vwap: Mapped[float | None] = mapped_column(Float)
    num_trades: Mapped[int | None] = mapped_column(Integer)
    turnover: Mapped[float | None] = mapped_column(Float)
    face_value: Mapped[float | None] = mapped_column(Float)  # on the settlement date
    accrued: Mapped[float | None] = mapped_column(Float)
    settle_date: Mapped[date | None] = mapped_column(Date)
    dema: Mapped[float | None] = mapped_column(Float)
    dema_half_life: Mapped[float | None] = mapped_column(Float)


class BondCandle(Base):
    """Hourly candles of bonds included in credit curves (input of DEMA)."""

    __tablename__ = "bond_candles"

    secid: Mapped[str] = mapped_column(ForeignKey("bonds.secid", ondelete="CASCADE"), primary_key=True)
    begin: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ------------------------------------------------------------------ credit curves
class CurveDefinition(Base):
    __tablename__ = "curve_definitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.emitter_id"))
    discount_curve_code: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    default_params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CurveBond(Base):
    """Composition of a curve: bonds chosen by the operator."""

    __tablename__ = "curve_bonds"

    curve_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curve_definitions.id", ondelete="CASCADE"), primary_key=True)
    secid: Mapped[str] = mapped_column(ForeignKey("bonds.secid"), primary_key=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    added_by: Mapped[str] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class _CurveSnapshot:
    as_of_date: Mapped[date] = mapped_column(Date)
    bonds: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)  # composition and observations used
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)  # requested parameters
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)  # discount / projection curves used
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    comment: Mapped[str] = mapped_column(Text, default="")


class CurveState(_CurveSnapshot, Base):
    __tablename__ = "curve_states"

    curve_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curve_definitions.id"), primary_key=True)
    saved_by: Mapped[str] = mapped_column(Text)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CurveHistory(_CurveSnapshot, Base):
    __tablename__ = "curve_history"
    __table_args__ = (UniqueConstraint("curve_id", "as_of_date", "version", name="uq_credit_curve_history_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    curve_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curve_definitions.id"))
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)  # snapshot | auto_archive | manual_edit
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ------------------------------------------------------------------ CDS pricing
class Calendar(Base):
    """Business-day calendar: weekdays plus exceptions; may extend a base calendar (SPFI = Moscow + sessions)."""

    __tablename__ = "calendars"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    base_code: Mapped[str | None] = mapped_column(ForeignKey("calendars.code"))
    description: Mapped[str] = mapped_column(Text, default="")


class CalendarDay(Base):
    __tablename__ = "calendar_days"

    calendar_code: Mapped[str] = mapped_column(ForeignKey("calendars.code", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    is_business: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CdsEntity(Base):
    """Reference entity of CDS contracts, priced from a bond credit curve."""

    __tablename__ = "cds_entities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    inn: Mapped[str | None] = mapped_column(Text)
    ogrn: Mapped[str | None] = mapped_column(Text)
    lei: Mapped[str | None] = mapped_column(Text)
    reference_obligation: Mapped[str | None] = mapped_column(Text)  # ISIN of the reference obligation
    credit_curve_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curve_definitions.id"))
    standard_coupon_bp: Mapped[int] = mapped_column(Integer, default=100)
    max_tenor: Mapped[str] = mapped_column(Text, default="5Y")
    recovery_override: Mapped[float | None] = mapped_column(Float)
    doc_basis_bp: Mapped[float] = mapped_column(Float, default=0.0)  # Sber vs SPFI documentation basis
    basis_bp: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # CDS–bond basis b(T) per tenor
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class _CdsSnapshot:
    as_of_date: Mapped[date] = mapped_column(Date)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)  # credit curve, discount curve, hazard, settings used
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trigger: Mapped[str] = mapped_column(Text)  # credit_curve | rates | entity | manual


class CdsState(_CdsSnapshot, Base):
    __tablename__ = "cds_states"

    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cds_entities.id", ondelete="CASCADE"), primary_key=True)
    calculated_by: Mapped[str] = mapped_column(Text)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CdsHistory(_CdsSnapshot, Base):
    __tablename__ = "cds_history"
    __table_args__ = (UniqueConstraint("entity_id", "as_of_date", "version", name="uq_cds_history_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cds_entities.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)  # snapshot | auto_archive | backdated
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateSnapshot(Base):
    """Interest-rate curves pushed by the curves service on save (input of automatic CDS recalculation)."""

    __tablename__ = "rate_snapshots"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    version: Mapped[int | None] = mapped_column(Integer)
    day_count: Mapped[str] = mapped_column(Text)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
