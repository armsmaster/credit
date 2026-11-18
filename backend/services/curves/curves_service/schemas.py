"""API contracts (pydantic). Rates are in percent, deviations in basis points."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain.quotes import InputMode

CODE_PATTERN = r"^[A-Z0-9][A-Z0-9_.\-]{1,63}$"


class CurrencyIn(BaseModel):
    code: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217, например RUB")
    name: str = Field(min_length=1, max_length=100)
    numeric_code: str | None = Field(None, pattern=r"^\d{3}$")
    is_active: bool = True


class CurrencyUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    numeric_code: str | None = Field(None, pattern=r"^\d{3}$")
    is_active: bool = True


class CurrencyOut(CurrencyIn):
    model_config = ConfigDict(from_attributes=True)
    created_at: datetime
    updated_at: datetime


class ParamsIn(BaseModel):
    """Calibration parameters; omitted fields fall back to the curve defaults."""

    max_avg_deviation_bp: float | None = Field(None, ge=0, le=500)
    max_point_deviation_bp: float | None = Field(None, ge=0, le=1000)
    smoothness: float | None = Field(None, ge=0, le=1e7)
    bid_ask_penalty: float | None = Field(None, ge=0, le=1e7)
    long_end_flat_from_months: int | None = Field(None, ge=1, le=600)
    long_end_flatness: float | None = Field(None, ge=0, le=1e7)
    short_end_months: int | None = Field(None, ge=0, le=120)
    short_end_weight: float | None = Field(None, gt=0, le=1)

    def overrides(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class DefinitionBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    index_name: str = Field("", max_length=100)
    description: str = Field("", max_length=2000)
    day_count: Literal["ACT/365F", "ACT/360"] = "ACT/365F"
    fixed_frequency_months: Literal[1, 3, 6, 12] = 12
    single_payment_max_months: int = Field(12, ge=0, le=24)
    default_params: ParamsIn = ParamsIn()
    is_active: bool = True


class DefinitionIn(DefinitionBase):
    code: str = Field(pattern=CODE_PATTERN, description="Кодовое имя кривой, например RUB-RUONIA-OIS")


class StateInfo(BaseModel):
    as_of_date: date
    saved_by: str
    saved_at: datetime


class DefinitionOut(DefinitionIn):
    id: str
    created_at: datetime
    updated_at: datetime
    effective_params: dict[str, Any]
    current_state: StateInfo | None = None
    last_history_date: date | None = None


class QuotesPayload(BaseModel):
    input_mode: InputMode
    quotes: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    params: ParamsIn = ParamsIn()


class CalibrateRequest(QuotesPayload):
    curve_code: str
    as_of_date: date


class SaveStateRequest(QuotesPayload):
    as_of_date: date
    comment: str = Field("", max_length=1000)


class SaveHistoryRequest(QuotesPayload):
    comment: str = Field(min_length=3, max_length=1000, description="Причина изменения задним числом")


class CalibrationOut(BaseModel):
    as_of_date: date
    params: dict[str, Any]
    conventions: dict[str, Any]
    nodes: list[dict[str, Any]]
    fitted: list[dict[str, Any]]
    stats: dict[str, Any]
    warnings: list[str]


class SnapshotOut(BaseModel):
    curve_code: str
    kind: Literal["state", "history"]
    as_of_date: date
    version: int | None = None
    source: str | None = None
    input_mode: InputMode
    quotes: list[dict[str, Any]]
    params: dict[str, Any]
    conventions: dict[str, Any]
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
    kind: Literal["state", "history"]
    version: int | None
    nodes: list[dict[str, Any]]


class EvaluateRequest(BaseModel):
    as_of_date: date
    day_count: Literal["ACT/365F", "ACT/360"] = "ACT/365F"
    nodes: list[dict[str, Any]] = Field(min_length=2, description="[{date, df}] узлы кривой")
    date: date
    start_date: date | None = None


class EvaluateOut(BaseModel):
    date: date
    t: float
    df: float
    zero_rates: dict[str, float]
    instantaneous_forward: float
    forward_rates: dict[str, float] | None = None
    extrapolated: bool


class TaskRunOut(BaseModel):
    task_id: str
