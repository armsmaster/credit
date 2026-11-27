"""Open read-only API: model CDS quotes at standard tenors (no token; mounted under /api/credit/public).

Model mid of a CDS = its par spread on the reference entity's CDS curve; QS and PUF at the standard coupons are the
same price in the SPFI quoting conventions.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cds_common.db import get_session
from cds_common.errors import NotFound

from .models import CdsEntity, CdsHistory, CdsState, CurveDefinition

router = APIRouter(prefix="/v1", tags=["Модельные котировки CDS"])


class CdsQuoteOut(BaseModel):
    tenor: str = Field(description="Стандартный срок", examples=["5Y"])
    maturity: date = Field(description="Плановая дата прекращения стандартного контракта СПФИ")
    par_spread_bp: float = Field(description="Модельный мид: par spread, б.п.", examples=[190.54])
    qs_bp: float = Field(description="Котировка спредом (QS): плоская кривая с Rq, б.п.", examples=[190.67])
    puf_pct: dict[str, float] = Field(description="Upfront (PUF) при стандартных купонах, % номинала (ключ — купон, б.п.; плюс — платит покупатель)",
                                      examples=[{"100": 3.2238, "500": -11.0185}])
    survival: float = Field(description="Вероятность дожития до плановой даты прекращения")
    excluded: bool = Field(description="Узел исключён из бутстрепа CDS-кривой (требовал λ ≤ 0); котировка модельная по соседним узлам")


class CdsCurveOut(BaseModel):
    entity: str = Field(description="Код контрольного лица", examples=["VEB"])
    name: str
    credit_curve: str | None = Field(description="Кредитная кривая облигаций, из которой построена CDS-кривая")
    standard_coupon_bp: int = Field(description="Стандартный купон контрольного лица, б.п.")
    recovery: float = Field(description="Ставка восстановления оценки")
    recovery_quote: float = Field(description="Rq конверсии QS ↔ PUF")
    conventions: str = Field(description="Конвенции стандартных контрактов", examples=["SPFI_STD"])
    as_of_date: date = Field(description="Дата CDS-кривой")
    effective_date: date = Field(description="Дата начала срока стандартных контрактов (T + 1 рабочий день)")
    requested_date: date | None = Field(description="Запрошенная дата (для запросов за дату)")
    stale: bool = Field(description="CDS-кривая на запрошенную дату не считалась — показана последняя более ранняя")
    kind: Literal["current", "history"] = Field(description="current — текущая кривая (онлайн), history — версия истории")
    version: int | None = Field(description="Версия истории (для kind = history)")
    published_at: datetime = Field(description="Когда кривая рассчитана")
    quotes: list[CdsQuoteOut]


def _snapshot(s: Session, entity: CdsEntity, as_of: date | None) -> CdsState | CdsHistory | None:
    """Online: the current CDS curve (else the latest history); for a date: the curve in force on it (latest <= date)."""
    state = s.get(CdsState, entity.id)
    stmt = select(CdsHistory).where(CdsHistory.entity_id == entity.id)
    if as_of is not None:
        stmt = stmt.where(CdsHistory.as_of_date <= as_of)
    hist = s.scalars(stmt.order_by(CdsHistory.as_of_date.desc(), CdsHistory.version.desc()).limit(1)).first()
    if state is not None and (as_of is None or state.as_of_date <= as_of) and (hist is None or state.as_of_date >= hist.as_of_date):
        return state
    return hist


def curve_out(entity: CdsEntity, snap: CdsState | CdsHistory, credit_curve: str | None, requested: date | None) -> dict[str, Any]:
    res, is_state = snap.result, isinstance(snap, CdsState)
    return {
        "entity": entity.code, "name": entity.name, "credit_curve": credit_curve, "standard_coupon_bp": entity.standard_coupon_bp,
        "recovery": res["recovery"], "recovery_quote": res["recovery_quote"],
        "conventions": (snap.inputs.get("conventions") or {}).get("name", "SPFI_STD"),
        "as_of_date": snap.as_of_date, "effective_date": res["effective_date"], "requested_date": requested,
        "stale": bool(requested and snap.as_of_date < requested), "kind": "current" if is_state else "history",
        "version": None if is_state else snap.version, "published_at": snap.calculated_at if is_state else snap.created_at,
        "quotes": [{"tenor": r["tenor"], "maturity": r["maturity"], "par_spread_bp": r["par_spread_bp"], "qs_bp": r["qs_bp"],
                    "puf_pct": {k: v * 100.0 for k, v in r["puf"].items()}, "survival": r["survival"], "excluded": bool(r.get("dropped"))}
                   for r in res["table"]],
    }


DATE_HELP = "Дата (YYYY-MM-DD): CDS-кривая, действовавшая на эту дату (последняя рассчитанная не позже неё). Пусто — текущая (онлайн)."


@router.get("/cds", response_model=list[CdsCurveOut], summary="Модельные котировки CDS по всем контрольным лицам")
def cds(date_: date | None = Query(None, alias="date", description=DATE_HELP),
        entity: list[str] = Query(default=[], description="Коды контрольных лиц (можно несколько); пусто — все активные"),
        s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Par spread, QS и PUF стандартных контрактов СПФИ на стандартных сроках — онлайн или за дату."""
    codes = dict(s.execute(select(CurveDefinition.id, CurveDefinition.code)).all())
    stmt = select(CdsEntity).where(CdsEntity.is_active.is_(True)).order_by(CdsEntity.code)
    if entity:
        stmt = stmt.where(CdsEntity.code.in_(entity))
    out = []
    for e in s.scalars(stmt):
        snap = _snapshot(s, e, date_)
        if snap is not None:
            out.append(curve_out(e, snap, codes.get(e.credit_curve_id), date_))
    return out


@router.get("/cds/{code}", response_model=CdsCurveOut, summary="Модельные котировки CDS одного контрольного лица")
def cds_entity(code: str, date_: date | None = Query(None, alias="date", description=DATE_HELP),
               s: Session = Depends(get_session)) -> dict[str, Any]:
    e = s.scalars(select(CdsEntity).where(CdsEntity.code == code)).first()
    if e is None:
        raise NotFound(f"Контрольное лицо {code} не найдено")
    snap = _snapshot(s, e, date_)
    if snap is None:
        raise NotFound(f"CDS-кривая {code} ещё не рассчитана" if date_ is None else f"Нет CDS-кривой {code} на {date_} или раньше")
    curve = s.get(CurveDefinition, e.credit_curve_id)
    return curve_out(e, snap, curve.code if curve else None, date_)
