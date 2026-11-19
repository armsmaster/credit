from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .domain.bonds import BondKind, RateIndex

CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{1,63}$"


# ------------------------------------------------------------------ issuers
class IssSearchOut(BaseModel):
    emitter_id: int
    title: str
    short_title: str | None = None
    inn: str | None = None
    tracked: bool


class IssuerAdd(BaseModel):
    emitter_id: int


class IssuerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    emitter_id: int
    title: str
    short_title: str
    inn: str | None
    url: str | None
    added_by: str
    added_at: datetime
    scan_status: str | None
    scan_error: str | None
    scanned_at: datetime | None
    bonds_total: int = 0
    bonds_eligible: int = 0
    bonds_recommended: int = 0
    bonds_incomplete: int = 0
    curves: list[str] = []


# ------------------------------------------------------------------ bonds
class BondManual(BaseModel):
    """Data entered by an operator; empty fields fall back to ISS."""

    kind: BondKind | None = None
    coupon_index: RateIndex | None = None
    spread_pct: float | None = Field(None, ge=-10, le=50)
    lag_days: int | None = Field(None, ge=0, le=60)
    fixed_rate_pct: float | None = Field(None, ge=0, le=100)
    notional_index: RateIndex | None = None
    offer_date: date | None = None
    offer_price: float | None = Field(None, gt=0, le=200)
    ignore_offer: bool = False
    force_eligible: bool = False
    excluded: bool = False
    dema_half_life_hours: float | None = Field(None, gt=0, le=1000)
    comment: str = Field("", max_length=1000)


class BondOut(BaseModel):
    secid: str
    isin: str | None
    shortname: str
    name: str
    issuer_id: int
    board: str | None
    is_traded: bool
    face_unit: str | None
    face_value: float | None
    initial_face: float | None
    coupon_pct: float | None
    list_level: int | None
    profile: dict[str, Any]
    years_to_maturity: float | None
    liquidity: dict[str, Any]
    liquidity_at: datetime | None
    recommended: bool
    notes: list[str]
    short: bool
    manual: dict[str, Any]
    manual_updated_by: str | None
    manual_updated_at: datetime | None
    curves: list[str]
    updated_at: datetime | None


class BondDetailOut(BondOut):
    desc: dict[str, Any]
    schedule: dict[str, Any]
    daily: list[dict[str, Any]]
    intraday: list[dict[str, Any]]


# ------------------------------------------------------------------ settings
class SettingsIn(BaseModel):
    index_curves: dict[RateIndex, str]
    liquidity: dict[str, float]
    dema: dict[str, float | None]


# ------------------------------------------------------------------ curve definitions
class ParamsIn(BaseModel):
    """Overrides of calibration parameters; absent fields keep the curve defaults."""

    recovery_rate: float | None = Field(None, ge=0, lt=1)
    smoothness: float | None = Field(None, ge=0)
    long_end_flatness: float | None = Field(None, ge=0)
    long_end_flat_from_months: int | None = Field(None, ge=1, le=120)
    secondary_weight: float | None = Field(None, ge=0, le=1)
    short_bond_years: float | None = Field(None, ge=0, le=5)

    def overrides(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class CurveBondIn(BaseModel):
    secid: str
    weight: float = Field(1.0, gt=0, le=100)


class DefinitionBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    issuer_id: int
    discount_curve_code: str = Field(min_length=1)
    description: str = ""
    default_params: ParamsIn = ParamsIn()
    bonds: list[CurveBondIn] = []
    is_active: bool = True

    @field_validator("bonds")
    @classmethod
    def unique_bonds(cls, v: list[CurveBondIn]) -> list[CurveBondIn]:
        if len({b.secid for b in v}) != len(v):
            raise ValueError("Бумага указана в составе дважды")
        return v


class DefinitionIn(DefinitionBase):
    code: str = Field(pattern=CODE_PATTERN)


class StateInfo(BaseModel):
    as_of_date: date
    saved_by: str
    saved_at: datetime


class DefinitionOut(BaseModel):
    id: str
    code: str
    name: str
    issuer_id: int
    issuer_title: str
    discount_curve_code: str
    description: str
    default_params: dict[str, Any]
    effective_params: dict[str, Any]
    bonds: list[dict[str, Any]]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    current_state: StateInfo | None = None
    last_history_date: date | None = None


# ------------------------------------------------------------------ calculation
ObsKind = Literal["dema", "bid", "ask", "last", "vwap"]


class CalcBondIn(BaseModel):
    secid: str
    weight: float = Field(1.0, gt=0, le=100)
    enabled: bool = True
    manual_price: float | None = Field(None, gt=0, le=1000)
    excluded_obs: list[ObsKind] = []


class CalcPayload(BaseModel):
    bonds: list[CalcBondIn] | None = None  # None: composition of the curve, all enabled
    params: ParamsIn = ParamsIn()


class CalibrateRequest(CalcPayload):
    curve_code: str
    as_of_date: date


class SaveStateRequest(CalcPayload):
    as_of_date: date
    comment: str = Field("", max_length=1000)


class SaveHistoryRequest(CalcPayload):
    comment: str = Field(min_length=3, max_length=1000)


class CalculationOut(BaseModel):
    as_of_date: date
    bonds: list[dict[str, Any]]
    inputs: dict[str, Any]
    result: dict[str, Any] | None


class SnapshotOut(BaseModel):
    curve_code: str
    kind: str  # state | history
    as_of_date: date
    version: int | None = None
    source: str | None = None
    bonds: list[dict[str, Any]]
    params: dict[str, Any]
    inputs: dict[str, Any]
    result: dict[str, Any]
    comment: str
    author: str
    saved_at: datetime


class HistoryDateOut(BaseModel):
    as_of_date: date
    latest_version: int
    versions: int
    source: str
    author: str
    saved_at: datetime
    edited: bool


class VersionOut(BaseModel):
    version: int
    source: str
    author: str
    saved_at: datetime
    comment: str


class CompareCurveOut(BaseModel):
    label: str
    target_date: date
    as_of_date: date
    kind: str
    version: int | None
    nodes: list[dict[str, Any]]


class TaskRunOut(BaseModel):
    task_id: str
