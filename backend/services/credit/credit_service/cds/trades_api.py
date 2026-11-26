from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cds_common.auth import Principal, require_operator, require_viewer
from cds_common.db import get_session

from .trades import TradeApp

router = APIRouter(prefix="/cds/trades", tags=["cds-trades"])


def get_trades(request: Request, session: Session = Depends(get_session)) -> TradeApp:
    return TradeApp(session, request.app.state.settings)


class ScheduleParamsIn(BaseModel):
    mode: Literal["standard", "custom"] = "standard"
    frequency_months: Literal[1, 3, 6, 12] = 3
    direction: Literal["backward", "forward"] = "backward"
    stub: Literal["short", "long"] = "short"
    roll_day: int | None = Field(None, ge=1, le=31)
    bdc: Literal["following", "modified_following", "preceding", "unadjusted"] = "following"
    adjust_accrual: bool = True
    calendar: str | None = None
    day_count: Literal["ACT/ACT.ISDA", "ACT/365F", "ACT/360"] = "ACT/ACT.ISDA"
    last_period_inclusive: bool = True


class RowIn(BaseModel):
    accrual_start: date
    accrual_end: date
    pay_date: date
    amount: float | None = None
    manual: bool = False  # the amount was typed by hand (otherwise it follows the dates)


class TermsIn(BaseModel):
    side: Literal["buyer", "seller"] = "buyer"
    notional: float = Field(gt=0)
    coupon_bp: float = Field(gt=0, le=10000)
    trade_date: date
    effective_date: date | None = None  # default: T + 1 business day with a rebate, T without
    maturity: date
    rebate: bool = True
    schedule_params: ScheduleParamsIn = ScheduleParamsIn()
    rows: list[RowIn] | None = None  # the hand-edited schedule; None — generate


class TradeIn(TermsIn):
    external_id: str = Field(min_length=1, max_length=100)
    entity_code: str
    tags: list[str] = []
    comment: str = Field("", max_length=1000)


def _dump(m: BaseModel) -> dict[str, Any]:
    data = m.model_dump()
    if data.get("rows") is not None:
        data["rows"] = [{**r, **{k: r[k].isoformat() for k in ("accrual_start", "accrual_end", "pay_date")}} for r in data["rows"]]
    return data


@router.get("/meta")
def meta(app: TradeApp = Depends(get_trades), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    return app.meta()


@router.get("/tags")
def tags(q: str | None = None, app: TradeApp = Depends(get_trades), _: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    return app.tags(q)


@router.get("/maturities")
def maturities(trade_date: date, effective_date: date | None = None, rebate: bool = True, calendar: str | None = None,
               mode: Literal["standard", "custom"] = "standard", app: TradeApp = Depends(get_trades),
               _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Expiration dates of the standard tenors (6M…10Y) for the quick buttons of the trade form."""
    return app.standard_maturities(trade_date, effective_date, rebate, calendar, mode)


@router.post("/schedule")
def schedule_preview(data: TermsIn, app: TradeApp = Depends(get_trades), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Generated schedule or the edited rows normalised (amounts follow the dates unless typed by hand)."""
    return app.build_schedule(_dump(data))


@router.get("")
def search(date_: date | None = Query(None, alias="date"), q: str | None = None, entity: str | None = None,
           tag: list[str] = Query(default=[]), side: str | None = None, status: str = "active",
           app: TradeApp = Depends(get_trades), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Trades with their value on the date (the latest calculation of that date)."""
    return app.search(date_ or app.settings.today(), q=q, entity_code=entity, tags=tag, side=side, status=status)


@router.post("", status_code=201)
def create(data: TradeIn, app: TradeApp = Depends(get_trades), user: Principal = Depends(require_operator)) -> dict[str, Any]:
    trade = app.save(None, _dump(data), user)
    return app.detail(trade.id)


@router.post("/revalue")
def revalue_all(date_: date | None = Query(None, alias="date"), app: TradeApp = Depends(get_trades),
                user: Principal = Depends(require_operator)) -> dict[str, Any]:
    """Value every live trade on the date now (latest CDS curve on or before it)."""
    return app.revalue(date_ or app.settings.today(), "manual", user)


@router.get("/{trade_id}")
def detail(trade_id: uuid.UUID, app: TradeApp = Depends(get_trades), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    return app.detail(trade_id)


@router.put("/{trade_id}")
def update(trade_id: uuid.UUID, data: TradeIn, app: TradeApp = Depends(get_trades), user: Principal = Depends(require_operator)) -> dict[str, Any]:
    app.save(trade_id, _dump(data), user)
    return app.detail(trade_id)


@router.post("/{trade_id}/cancel")
def cancel(trade_id: uuid.UUID, app: TradeApp = Depends(get_trades), user: Principal = Depends(require_operator)) -> dict[str, Any]:
    app.set_status(trade_id, "cancelled", user)
    return app.detail(trade_id)


@router.post("/{trade_id}/restore")
def restore(trade_id: uuid.UUID, app: TradeApp = Depends(get_trades), user: Principal = Depends(require_operator)) -> dict[str, Any]:
    app.set_status(trade_id, "active", user)
    return app.detail(trade_id)


@router.post("/{trade_id}/revalue")
def revalue_one(trade_id: uuid.UUID, date_: date | None = Query(None, alias="date"), app: TradeApp = Depends(get_trades),
                user: Principal = Depends(require_operator)) -> dict[str, Any]:
    trade = app.get(trade_id)
    app.value_trade(trade, date_ or app.settings.today(), "manual", user)
    app.s.commit()
    return app.detail(trade_id)


@router.get("/{trade_id}/valuations")
def valuations(trade_id: uuid.UUID, date_: date | None = Query(None, alias="date"), app: TradeApp = Depends(get_trades),
               _: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    """Every calculation (intraday and end-of-day), the latest first."""
    return app.valuation_records(trade_id, date_)


@router.get("/{trade_id}/versions/{version}")
def version(trade_id: uuid.UUID, version: int, app: TradeApp = Depends(get_trades), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    return app.version_detail(trade_id, version)
