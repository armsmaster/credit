"""Open read-only API: model mids of the quoted interest-rate swaps (no token; mounted under /api/curves/public).

Model mid = the adjusted ("fitted") mid of a curve at a quoted tenor: the par rate of the swap on the calibrated curve.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cds_common.db import get_session
from cds_common.errors import NotFound

from .models import CurveDefinition, CurveHistory, CurveState

router = APIRouter(prefix="/v1", tags=["Модельные миды свопов"])


class SwapQuoteOut(BaseModel):
    tenor: str = Field(description="Котируемый срок", examples=["1Y"])
    maturity: date = Field(description="Дата окончания свопа")
    model_mid_pct: float = Field(description="Модельный мид: par-ставка свопа на построенной кривой, % годовых", examples=[15.8734])


class SwapCurveOut(BaseModel):
    curve: str = Field(description="Код кривой", examples=["RUB-RUONIA-OIS"])
    name: str
    currency: str = Field(examples=["RUB"])
    index: str = Field(description="Плавающий индекс")
    day_count: str = Field(description="Коэффициент дней фиксированной ноги", examples=["ACT/365F"])
    fixed_frequency_months: int = Field(description="Частота фиксированных платежей, мес.")
    single_payment_max_months: int = Field(description="До этого срока включительно — один платёж в конце")
    as_of_date: date = Field(description="Дата кривой, по которой посчитаны миды")
    requested_date: date | None = Field(description="Запрошенная дата (для запросов за дату)")
    stale: bool = Field(description="Кривая на запрошенную дату не строилась — показана последняя более ранняя")
    kind: Literal["current", "history"] = Field(description="current — текущая кривая (онлайн), history — версия истории")
    version: int | None = Field(description="Версия истории (для kind = history)")
    published_at: datetime = Field(description="Когда кривая сохранена")
    quotes: list[SwapQuoteOut]


def _snapshot(s: Session, defn: CurveDefinition, as_of: date | None) -> CurveState | CurveHistory | None:
    """Online: the current state (else the latest history); for a date: the curve in force on it (latest <= date)."""
    state = s.get(CurveState, defn.id)
    stmt = select(CurveHistory).where(CurveHistory.curve_id == defn.id)
    if as_of is not None:
        stmt = stmt.where(CurveHistory.as_of_date <= as_of)
    hist = s.scalars(stmt.order_by(CurveHistory.as_of_date.desc(), CurveHistory.version.desc()).limit(1)).first()
    if state is not None and (as_of is None or state.as_of_date <= as_of) and (hist is None or state.as_of_date >= hist.as_of_date):
        return state
    return hist


def curve_out(defn: CurveDefinition, snap: CurveState | CurveHistory, requested: date | None) -> dict[str, Any]:
    is_state = isinstance(snap, CurveState)
    return {
        "curve": defn.code, "name": defn.name, "currency": defn.currency_code, "index": defn.index_name, "day_count": defn.day_count,
        "fixed_frequency_months": defn.fixed_frequency_months, "single_payment_max_months": defn.single_payment_max_months,
        "as_of_date": snap.as_of_date, "requested_date": requested, "stale": bool(requested and snap.as_of_date < requested),
        "kind": "current" if is_state else "history", "version": None if is_state else snap.version,
        "published_at": snap.saved_at if is_state else snap.created_at,
        "quotes": [{"tenor": f["tenor"], "maturity": f["maturity"], "model_mid_pct": f["model"]} for f in (snap.result or {}).get("fitted", [])],
    }


DATE_HELP = "Дата (YYYY-MM-DD): кривая, действовавшая на эту дату (последняя построенная не позже неё). Пусто — текущая кривая (онлайн)."


@router.get("/swaps", response_model=list[SwapCurveOut], summary="Модельные миды свопов по всем кривым")
def swaps(request: Request, date_: date | None = Query(None, alias="date", description=DATE_HELP),
          curve: list[str] = Query(default=[], description="Коды кривых (можно несколько); пусто — все активные"),
          s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Модельные миды процентных свопов на котируемых сроках каждой активной кривой — онлайн или за дату."""
    stmt = select(CurveDefinition).where(CurveDefinition.is_active.is_(True)).order_by(CurveDefinition.code)
    if curve:
        stmt = stmt.where(CurveDefinition.code.in_(curve))
    out = []
    for defn in s.scalars(stmt):
        snap = _snapshot(s, defn, date_)
        if snap is not None:
            out.append(curve_out(defn, snap, date_))
    return out


@router.get("/swaps/{code}", response_model=SwapCurveOut, summary="Модельные миды свопов одной кривой")
def swap_curve(code: str, date_: date | None = Query(None, alias="date", description=DATE_HELP),
               s: Session = Depends(get_session)) -> dict[str, Any]:
    defn = s.scalars(select(CurveDefinition).where(CurveDefinition.code == code)).first()
    if defn is None:
        raise NotFound(f"Кривая {code} не найдена")
    snap = _snapshot(s, defn, date_)
    if snap is None:
        raise NotFound(f"Кривая {code} ещё не построена" if date_ is None else f"Нет кривой {code} на {date_} или раньше")
    return curve_out(defn, snap, date_)
