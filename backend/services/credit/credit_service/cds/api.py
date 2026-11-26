from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cds_common.auth import Principal, require_operator, require_viewer
from cds_common.db import get_session
from cds_common.errors import AppError
from cds_common.xlsx import XLSX_MEDIA_TYPE

from ..api import get_rates
from ..models import CdsEntity, CdsHistory, CdsState, CurveDefinition, CurveState
from ..rates_client import RatesClient
from . import export_xlsx
from .application import TRIGGERS, CdsApp
from .isda import TENOR_MONTHS

router = APIRouter(prefix="/cds", tags=["cds"])
calendars = APIRouter(prefix="/calendars", tags=["calendars"])


def get_cds(request: Request, session: Session = Depends(get_session)) -> CdsApp:
    return CdsApp(session, request.app.state.settings)


# ---------------------------------------------------------------- schemas
class CdsSettingsIn(BaseModel):
    recovery_quote: float | None = Field(None, ge=0, lt=1)
    tenors: list[str] | None = None
    coupons_bp: list[int] | None = None
    day_count: Literal["ACT/ACT.ISDA", "ACT/365F", "ACT/360"] | None = None
    protection_from_effective: bool | None = None
    rebate_mode: Literal["spec", "unadjusted_start", "inclusive_end"] | None = None
    sber_adjust_period_ends: bool | None = None
    sber_last_period_inclusive: bool | None = None
    spfi_calendar: str | None = None
    sber_calendar: str | None = None


class EntityIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}$")
    name: str = Field(min_length=1, max_length=200)
    inn: str | None = None
    ogrn: str | None = None
    lei: str | None = None
    reference_obligation: str | None = None
    credit_curve_code: str
    standard_coupon_bp: int = Field(100, gt=0, le=5000)
    max_tenor: str = "5Y"
    recovery_override: float | None = Field(None, ge=0, lt=1)
    doc_basis_bp: float = Field(0.0, ge=-1000, le=1000)
    basis_bp: dict[str, float | None] = {}
    is_active: bool = True


class DayIn(BaseModel):
    is_business: bool
    comment: str = Field("", max_length=300)


class ContractIn(BaseModel):
    entity_code: str
    as_of_date: date | None = None  # curve date; default — the current curve
    profile: Literal["SPFI_STD", "SBER_OTC"] = "SPFI_STD"
    trade_date: date | None = None
    maturity: date | None = None
    tenor: str | None = None
    coupon_bp: float | None = Field(None, ge=0, le=10000)
    notional: float | None = Field(None, gt=0)
    side: Literal["buyer", "seller"] = "buyer"
    upfront_pct: float | None = Field(None, ge=-100, le=100)  # trade price as upfront (PUF), % of notional
    qs_bp: float | None = Field(None, gt=0, le=20000)  # trade price as quoted spread (QS)
    quote_input: Literal["qs", "puf"] | None = None  # which of the two the user entered (the other one is derived)
    recovery: float | None = Field(None, ge=0, lt=1)


def _entity_out(e: CdsEntity, codes: dict, state: CdsState | None) -> dict[str, Any]:
    out = {k: getattr(e, k) for k in ("code", "name", "inn", "ogrn", "lei", "reference_obligation", "standard_coupon_bp", "max_tenor",
                                       "recovery_override", "doc_basis_bp", "basis_bp", "is_active", "created_at", "updated_at")}
    out["credit_curve_code"] = codes.get(e.credit_curve_id)
    out["state"] = None
    if state is not None:
        table = state.result.get("table", [])
        out["state"] = {
            "as_of_date": state.as_of_date, "calculated_at": state.calculated_at, "calculated_by": state.calculated_by,
            "trigger": state.trigger, "trigger_title": TRIGGERS.get(state.trigger, state.trigger),
            "recovery": state.result.get("recovery"),
            "tenors": [{"tenor": r["tenor"], "par_spread_bp": r["par_spread_bp"], "qs_bp": r["qs_bp"], "puf": r["puf"]} for r in table],
            "warnings": state.result.get("warnings", []),
        }
    return out


def _snapshot_out(e: CdsEntity, row: CdsState | CdsHistory) -> dict[str, Any]:
    is_state = isinstance(row, CdsState)
    return {
        "entity_code": e.code, "entity_name": e.name, "kind": "state" if is_state else "history", "as_of_date": row.as_of_date,
        "version": None if is_state else row.version, "source": None if is_state else row.source, "trigger": row.trigger,
        "trigger_title": TRIGGERS.get(row.trigger, row.trigger),
        "author": row.calculated_by if is_state else row.created_by, "saved_at": row.calculated_at if is_state else row.created_at,
        "inputs": row.inputs, "result": row.result,
    }


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(content, media_type=XLSX_MEDIA_TYPE, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _recalc(app: CdsApp, code: str, user: Principal, rates: RatesClient, trigger: str) -> CdsState:
    """Recalculate the current CDS curve; fetch the discount curve when the credit curve was saved without its nodes."""
    try:
        return app.recalc_entity(code, user, trigger)
    except AppError as exc:
        if exc.code != "cds_no_discount":
            raise
        app.s.rollback()
        entity = app.get_entity(code)
        defn = app.s.get(CurveDefinition, entity.credit_curve_id)
        state = app.s.get(CurveState, defn.id)
        _, ref = rates.curve(defn.discount_curve_code, state.as_of_date)
        app.on_rates({**ref, "kind": "cache"}, user)  # keep the curve for automatic recalculations
        return app.recalc_entity(code, user, trigger, rates=ref)


# ---------------------------------------------------------------- meta and settings
@router.get("/meta")
def meta(request: Request, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    return {"today": request.app.state.settings.today(), "settings": app.config(), "tenors": list(TENOR_MONTHS),
            "calendars": app.list_calendars(), "profiles": {"SPFI_STD": "СПФИ (стандартный купон + upfront)", "SBER_OTC": "Сбер (par-купон с даты сделки)"}}


@router.put("/settings")
def update_settings(data: CdsSettingsIn, app: CdsApp = Depends(get_cds), user: Principal = Depends(require_operator)):
    return app.update_settings(data.model_dump(exclude_none=True), user)


# ---------------------------------------------------------------- reference entities and curves
@router.get("/entities")
def list_entities(app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    codes, states = app.credit_curve_codes(), app.state_infos()
    return [_entity_out(e, codes, states.get(e.id)) for e in app.list_entities()]


@router.post("/entities", status_code=201)
def create_entity(data: EntityIn, app: CdsApp = Depends(get_cds), rates: RatesClient = Depends(get_rates),
                  user: Principal = Depends(require_operator)) -> dict[str, Any]:
    entity = app.save_entity(None, data.model_dump(), user)
    return _after_save(app, entity.code, user, rates)


@router.put("/entities/{code}")
def update_entity(code: str, data: EntityIn, app: CdsApp = Depends(get_cds), rates: RatesClient = Depends(get_rates),
                  user: Principal = Depends(require_operator)) -> dict[str, Any]:
    app.save_entity(code, data.model_dump(), user)
    return _after_save(app, code, user, rates)


def _after_save(app: CdsApp, code: str, user: Principal, rates: RatesClient) -> dict[str, Any]:
    warning = None
    try:
        _recalc(app, code, user, rates, "entity")
    except AppError as exc:
        app.s.rollback()
        warning = exc.detail
    entity = app.get_entity(code)
    out = _entity_out(entity, app.credit_curve_codes(), app.state_infos().get(entity.id))
    out["warning"] = warning
    return out


@router.get("/entities/{code}")
def get_entity(code: str, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    entity = app.get_entity(code)
    return _entity_out(entity, app.credit_curve_codes(), app.state_infos().get(entity.id))


@router.post("/entities/{code}/recalculate")
def recalculate(code: str, app: CdsApp = Depends(get_cds), rates: RatesClient = Depends(get_rates),
                user: Principal = Depends(require_operator)) -> dict[str, Any]:
    state = _recalc(app, code, user, rates, "manual")
    return _snapshot_out(app.get_entity(code), state)


@router.get("/entities/{code}/state")
def get_state(code: str, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    entity, state = app.get_state(code)
    return _snapshot_out(entity, state)


@router.get("/entities/{code}/history")
def list_history(code: str, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    return app.list_history(code)


@router.get("/entities/{code}/history/{as_of}")
def get_history(code: str, as_of: date, version: int | None = None, app: CdsApp = Depends(get_cds),
                _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    entity, row = app.get_history(code, as_of, version)
    return _snapshot_out(entity, row)


# ---------------------------------------------------------------- calculator and files
@router.post("/calculator")
def calculator(req: ContractIn, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Any contract on the CDS curve of a reference entity: schedule, rebate, PUF/QS/par, NPV of the given terms."""
    return app.calculate_contract(req.model_dump())


@router.post("/calculator/quote")
def calculator_quote(req: ContractIn, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """QS ↔ upfront (PUF) of the contract by the ISDA convention: the flat hazard curve with recovery Rq that prices the quote."""
    return app.convert_quote(req.model_dump())


@router.post("/calculator/export.xlsx")
def calculator_excel(req: ContractIn, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)):
    res = app.calculate_contract(req.model_dump())
    return _xlsx(export_xlsx.build_contract(res), f"CDS_{res['entity_code']}_{res['maturity']}_{res['profile']}.xlsx")


@router.get("/entities/{code}/state/export.xlsx")
def state_excel(code: str, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)):
    entity, state = app.get_state(code)
    return _xlsx(export_xlsx.build_curve(_snapshot_out(entity, state)), f"CDS_{entity.code}_{state.as_of_date}.xlsx")


@router.get("/entities/{code}/history/{as_of}/export.xlsx")
def history_excel(code: str, as_of: date, version: int | None = None, app: CdsApp = Depends(get_cds),
                  _: Principal = Depends(require_viewer)):
    entity, row = app.get_history(code, as_of, version)
    return _xlsx(export_xlsx.build_curve(_snapshot_out(entity, row)), f"CDS_{entity.code}_{as_of}_v{row.version}.xlsx")


@router.get("/daily.xlsx")
def daily_file(date: date, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)):
    """Results of the day for the position keeping system: discount factors, survival / hazard, CDS quotes."""
    rows = [(e, _snapshot_out(e, snap)) for e, snap in app.daily(date)]
    return _xlsx(export_xlsx.build_daily(date, rows, app.credit_curve_codes()), f"CDS_daily_{date}.xlsx")


# ---------------------------------------------------------------- calendars
@calendars.get("")
def list_calendars(app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    return app.list_calendars()


@calendars.get("/{code}/days")
def calendar_days(code: str, year: int, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    return [{"day": d.day, "is_business": d.is_business, "comment": d.comment, "updated_by": d.updated_by, "updated_at": d.updated_at}
            for d in app.calendar_days(code, year)]


@calendars.put("/{code}/days/{day}", status_code=204)
def set_day(code: str, day: date, data: DayIn, app: CdsApp = Depends(get_cds), user: Principal = Depends(require_operator)):
    app.set_day(code, day, data.is_business, data.comment, user)
    return Response(status_code=204)


@calendars.delete("/{code}/days/{day}", status_code=204)
def delete_day(code: str, day: date, app: CdsApp = Depends(get_cds), user: Principal = Depends(require_operator)):
    app.delete_day(code, day, user)
    return Response(status_code=204)


@calendars.get("/{code}/shift")
def shift(code: str, date: date, days: int = 1, app: CdsApp = Depends(get_cds), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    cal = app.calendar(code)
    return {"date": date, "days": days, "result": cal.add_business_days(date, days), "following": cal.following(date),
            "is_business": cal.is_business_day(date)}
