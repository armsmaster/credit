"""CDS trades: manual entry with versions and tags, schedules, theoretical value on a date.

Theoretical value of a trade on date D (protection buyer's side, with the sign of the trade's side):
the CDS curve of the reference entity — the latest one calculated on or before D — is moved to D
(conditional survival from D, discount factors re-based to D), and the remaining contract is priced
by the ISDA model on the trade's own schedule: NPV = N·(V_prot − c·RPV01_dirty), the accrued premium,
the clean value, the par spread of the remaining contract, the risky annuity and CS01 ≈ N·RPV01·1 bp.

When values are (re)calculated:
* a trade is saved (a new version) — the value for today;
* a CDS curve of the entity is recalculated (after a credit or interest-rate curve is saved) — today;
  a backdated CDS curve — the value for its date (task credit.trades_revalue);
* at the end of every business day (task credit.trades_eod) — the end-of-day value;
* on request for any date.
Each recalculation is a new row; the latest row of a date is the value of that date.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from cds_common.audit import AuditLog, diff
from cds_common.auth import Principal
from cds_common.errors import AppError, Conflict, NotFound
from cds_common.tasks import Trigger, enqueue

from ..config import CreditSettings
from ..models import CdsEntity, CdsHistory, CdsState, CdsTrade, CdsTradeTag, CdsTradeValuation, CdsTradeVersion, TradeTag
from .application import CdsApp
from .isda import SPFI, DiscountCurve, HazardCurve, cds_legs, price_legs, with_settings, yf_act365f
from .schedule import ScheduleParams, generate, rows_of, schedule_from_rows

audit = AuditLog("credit")
log = logging.getLogger(__name__)

REVALUE_TASK = "credit.trades_revalue"
VALUATION_TRIGGERS = {
    "trade_saved": "сохранена сделка",
    "cds_curve": "пересчитана CDS-кривая",
    "backdated": "CDS-кривая изменена задним числом",
    "eod": "итог дня",
    "manual": "оценка по запросу",
}
_TAG_RE = re.compile(r"\s+")


def rebase(hc: HazardCurve, dc: DiscountCurve, to: date) -> tuple[HazardCurve, DiscountCurve]:
    """Curves of date C seen from D >= C: survival conditional on no default by D, DF(D, ·) = DF(C, ·)/DF(C, D)."""
    if to <= hc.base:
        return hc, dc
    tau = yf_act365f(hc.base, to)
    times = [t - tau for t in hc.times if t > tau]
    lams = [lam for t, lam in zip(hc.times, hc.lambdas) if t > tau] or [hc.lambdas[-1]]
    hc2 = HazardCurve(to, times or [100.0], lams)
    later = [d for d in dc.dates if d > to]
    if not later:
        raise AppError("Кривая дисконтирования заканчивается раньше даты оценки", code="trade_no_discount")
    d0 = dc.df(to)
    return hc2, DiscountCurve(to, later, [dc.df(d) / d0 for d in later])


def value_on(version: CdsTradeVersion, d: date, snap: CdsState | CdsHistory, cfg: dict[str, Any]) -> dict[str, Any]:
    """Theoretical value of a trade version on date d from a CDS curve snapshot (see the module docstring)."""
    res, inputs = snap.result, snap.inputs
    c_date = snap.as_of_date
    hc = HazardCurve(c_date, [float(h["t"]) for h in res["hazard"]], [float(h["lambda"]) for h in res["hazard"]])
    nodes = inputs["discount"]["nodes"]
    dc = DiscountCurve(c_date, [date.fromisoformat(n["date"]) for n in nodes], [float(n["df"]) for n in nodes])
    hc, dc = rebase(hc, dc, d)
    recovery = float(res["recovery"])
    params = ScheduleParams.from_dict(version.schedule_params)
    conv = with_settings(SPFI, day_count=params.day_count, last_period_inclusive=params.last_period_inclusive,
                         protection_from_effective=bool(cfg.get("protection_from_effective", False)))
    sch = version.schedule
    sched = schedule_from_rows(version.trade_date, version.effective_date, version.maturity, sch["rows"], float(sch.get("rebate_year_frac") or 0.0),
                               date.fromisoformat(sch["settle_date"]),
                               date.fromisoformat(sch["rebate_start"]) if sch.get("rebate_start") else None,
                               date.fromisoformat(sch["rebate_end"]) if sch.get("rebate_end") else None)
    coupon = version.coupon_bp / 1e4
    sign = 1.0 if version.side == "buyer" else -1.0
    n = version.notional
    legs = cds_legs(sched, d, recovery, conv, dc, hc)
    pr = price_legs(legs, coupon)
    npv = sign * n * pr["pv_dirty"]
    accrued = -sign * n * coupon * legs.accrued_frac * legs.df_settle  # premium accrued and owed by the buyer
    nxt = next((r for r in sch["rows"] if date.fromisoformat(r["pay_date"]) > d), None)
    return {
        "npv": npv, "clean": npv - accrued, "accrued": accrued, "par_spread_bp": pr["par_spread"] * 1e4,
        "rpv01": pr["rpv01_clean"], "cs01": sign * n * pr["rpv01_clean"] * 1e-4,
        "details": {
            "protection_pv": sign * n * pr["protection"], "premium_pv": -sign * n * coupon * pr["rpv01_dirty"],
            "rpv01_dirty": pr["rpv01_dirty"], "accrued_year_frac": legs.accrued_frac, "df_settle": legs.df_settle,
            "survival_to_maturity": hc.survival(version.maturity), "pd": 1.0 - hc.survival(version.maturity),
            "recovery": recovery, "default_payment": sign * n * (1.0 - recovery), "protection_start": legs.protection_start.isoformat(),
            "curve_date": c_date.isoformat(), "curve_kind": "state" if isinstance(snap, CdsState) else "history",
            "curve_version": getattr(snap, "version", None), "stale_days": (d - c_date).days,
            "discount": {k: inputs["discount"].get(k) for k in ("code", "as_of_date", "kind", "version")},
            "next_payment": {"date": nxt["pay_date"], "amount": -sign * float(nxt["amount"])} if nxt else None,
        },
    }


class TradeApp:
    def __init__(self, session: Session, settings: CreditSettings):
        self.s = session
        self.settings = settings
        self.cds = CdsApp(session, settings)

    # ------------------------------------------------------------------ reference data
    def business_calendar_code(self) -> str:
        return self.cds.config().get("sber_calendar") or "MOSCOW"

    def meta(self) -> dict[str, Any]:
        cfg = self.cds.config()
        today = self.settings.today()
        cal = self.cds.calendar(self.business_calendar_code())
        prev = today - timedelta(days=1)
        while not cal.is_business_day(prev):
            prev -= timedelta(days=1)
        return {
            "today": today, "previous_business_day": prev, "business_calendar": cal.code,
            "calendars": self.cds.list_calendars(),
            "defaults": {"coupon_bp": 100, "calendar": cfg["spfi_calendar"], "day_count": cfg["day_count"]},
            "entities": [{"code": e.code, "name": e.name, "standard_coupon_bp": e.standard_coupon_bp}
                         for e in self.cds.list_entities() if e.is_active],
            "triggers": VALUATION_TRIGGERS,
        }

    def tags(self, q: str | None, limit: int = 20) -> list[dict[str, Any]]:
        usage = func.count(CdsTradeTag.trade_id)
        stmt = select(TradeTag.name, usage).outerjoin(CdsTradeTag, CdsTradeTag.tag_id == TradeTag.id).group_by(TradeTag.id)
        if q:
            stmt = stmt.where(TradeTag.name.ilike(f"%{q.strip()}%"))
        return [{"name": n, "trades": c} for n, c in self.s.execute(stmt.order_by(usage.desc(), TradeTag.name).limit(limit)).all()]

    @staticmethod
    def _clean_tags(raw: list[str]) -> list[str]:
        out: dict[str, str] = {}
        for t in raw or []:
            name = _TAG_RE.sub(" ", str(t)).strip()
            if not name:
                continue
            if len(name) > 50:
                raise AppError(f"Тег длиннее 50 символов: {name[:20]}…", code="invalid_trade")
            out.setdefault(name.lower(), name)
        return list(out.values())

    def _resolve_tags(self, names: list[str], actor: Principal) -> list[TradeTag]:
        tags = []
        for name in names:
            tag = self.s.scalars(select(TradeTag).where(func.lower(TradeTag.name) == name.lower())).first()
            if tag is None:
                tag = TradeTag(name=name, created_by=actor.username)
                self.s.add(tag)
                self.s.flush()
            tags.append(tag)
        return tags

    # ------------------------------------------------------------------ schedule
    def _terms(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validated economic terms with the effective date filled in."""
        try:
            params = ScheduleParams.from_dict(data.get("schedule_params"))
        except (TypeError, ValueError) as exc:
            raise AppError(str(exc), code="invalid_trade") from None
        if not params.calendar:
            params.calendar = self.cds.config()["spfi_calendar"]
        cal = self.cds.calendar(params.calendar)
        t = data["trade_date"]
        eff = data.get("effective_date") or (cal.add_business_days(t, 1) if data.get("rebate") else t)
        if eff < t:
            raise AppError("Дата начала срока не может быть раньше даты сделки", code="invalid_trade")
        if data["maturity"] <= eff:
            raise AppError("Дата экспирации должна быть позже даты начала срока", code="invalid_trade")
        if float(data["notional"]) <= 0 or float(data["coupon_bp"]) <= 0:
            raise AppError("Номинал и купон должны быть положительными", code="invalid_trade")
        return {**data, "effective_date": eff, "schedule_params": params.to_dict(), "_params": params, "_cal": cal}

    def build_schedule(self, data: dict[str, Any]) -> dict[str, Any]:
        """Generated schedule, or the user's rows normalised: amounts follow the dates unless typed by hand."""
        terms = self._terms(data)
        params, cal = terms["_params"], terms["_cal"]
        n, coupon = float(terms["notional"]), float(terms["coupon_bp"]) / 1e4
        try:
            gen = generate(terms["trade_date"], terms["effective_date"], terms["maturity"], bool(terms["rebate"]), params, cal,
                           self.cds.config().get("rebate_mode", "spec") if params.mode == "standard" else "spec", settle=terms["effective_date"])
        except ValueError as exc:
            raise AppError(str(exc), code="invalid_trade") from None
        generated = rows_of(gen, n, coupon, params.last_period_inclusive)
        rows, edited = generated, False
        if data.get("rows"):
            rows, edited = self._normalise_rows(data["rows"], terms, generated)
        return {
            "rows": rows, "edited": edited, "effective_date": terms["effective_date"].isoformat(),
            "settle_date": terms["effective_date"].isoformat(), "first_period_start": rows[0]["accrual_start"] if rows else None,
            "rebate_start": gen.rebate_start.isoformat() if gen.rebate_start else None,
            "rebate_end": gen.rebate_end.isoformat() if gen.rebate_end else None,
            "rebate_year_frac": gen.rebate_year_frac, "rebate_amount": -n * coupon * gen.rebate_year_frac,
            "total_amount": sum(r["amount"] for r in rows), "schedule_params": terms["schedule_params"],
        }

    def _normalise_rows(self, raw: list[dict[str, Any]], terms: dict[str, Any], generated: list[dict[str, Any]]) -> tuple[list, bool]:
        from .isda import DAY_COUNTS

        params = terms["_params"]
        dc = DAY_COUNTS[params.day_count]
        n, coupon = float(terms["notional"]), float(terms["coupon_bp"]) / 1e4
        rows = []
        for i, r in enumerate(raw):
            s, e, p = (date.fromisoformat(str(r[k])) for k in ("accrual_start", "accrual_end", "pay_date"))
            if e <= s:
                raise AppError(f"Период {i + 1}: конец должен быть позже начала", code="invalid_schedule")
            if rows and s != date.fromisoformat(rows[-1]["accrual_end"]):
                raise AppError(f"Период {i + 1}: начало должно совпадать с концом предыдущего", code="invalid_schedule")
            last = i == len(raw) - 1
            frac = dc(s, e + timedelta(days=1)) if last and params.last_period_inclusive else dc(s, e)
            manual = bool(r.get("manual")) and r.get("amount") is not None
            amount = float(r["amount"]) if manual else n * coupon * frac
            rows.append({"accrual_start": s.isoformat(), "accrual_end": e.isoformat(), "pay_date": p.isoformat(),
                         "days": (e - s).days + (1 if last and params.last_period_inclusive else 0),
                         "year_frac": amount / (n * coupon), "amount": amount, "manual": manual})
        if not rows:
            raise AppError("В графике нет периодов", code="invalid_schedule")
        if date.fromisoformat(rows[-1]["accrual_end"]) != terms["maturity"]:
            raise AppError("Последний период должен заканчиваться датой экспирации", code="invalid_schedule")
        keys = ("accrual_start", "accrual_end", "pay_date")
        edited = len(rows) != len(generated) or any(r["manual"] or any(r[k] != g[k] for k in keys) for r, g in zip(rows, generated))
        return rows, edited

    # ------------------------------------------------------------------ trades
    def get(self, trade_id: Any) -> CdsTrade:
        trade = self.s.get(CdsTrade, trade_id)
        if not trade:
            raise NotFound("Сделка не найдена")
        return trade

    def current_version(self, trade: CdsTrade) -> CdsTradeVersion:
        return self.s.scalars(select(CdsTradeVersion).where(CdsTradeVersion.trade_id == trade.id,
                                                            CdsTradeVersion.version == trade.version)).one()

    def save(self, trade_id: Any | None, data: dict[str, Any], actor: Principal) -> CdsTrade:
        external_id = str(data["external_id"]).strip()
        if not external_id:
            raise AppError("Укажите External ID", code="invalid_trade")
        entity = self.cds.get_entity(data["entity_code"])
        schedule = self.build_schedule(data)
        terms = self._terms(data)
        tags = self._clean_tags(data.get("tags") or [])
        clash = self.s.scalars(select(CdsTrade).where(CdsTrade.external_id == external_id)).first()
        if trade_id is None:
            if clash:
                raise Conflict(f"Сделка с External ID {external_id} уже есть")
            trade = CdsTrade(external_id=external_id, entity_id=entity.id, version=1, status="active",
                             created_by=actor.username, updated_by=actor.username)
            self.s.add(trade)
            self.s.flush()
            before = None
        else:
            trade = self.s.get(CdsTrade, trade_id, with_for_update=True)
            if not trade:
                raise NotFound("Сделка не найдена")
            if clash and clash.id != trade.id:
                raise Conflict(f"Сделка с External ID {external_id} уже есть")
            before = self._audit_view(self.current_version(trade))
            trade.version += 1
            trade.external_id, trade.entity_id, trade.updated_by = external_id, entity.id, actor.username
            trade.updated_at = datetime.now(timezone.utc)
        version = CdsTradeVersion(
            trade_id=trade.id, version=trade.version, external_id=external_id, entity_id=entity.id, side=data["side"],
            notional=float(data["notional"]), coupon_bp=float(data["coupon_bp"]), trade_date=terms["trade_date"],
            effective_date=terms["effective_date"], maturity=terms["maturity"], rebate=bool(data["rebate"]),
            schedule_params=terms["schedule_params"],
            schedule={k: schedule[k] for k in ("rows", "settle_date", "rebate_start", "rebate_end", "rebate_year_frac")},
            schedule_edited=schedule["edited"], tags=tags, comment=(data.get("comment") or "").strip(), created_by=actor.username)
        self.s.add(version)
        self.s.query(CdsTradeTag).filter(CdsTradeTag.trade_id == trade.id).delete()
        for tag in self._resolve_tags(tags, actor):
            self.s.add(CdsTradeTag(trade_id=trade.id, tag_id=tag.id))
        self.s.flush()
        after = self._audit_view(version)
        audit.record(self.s, actor=actor, entity_type="cds_trade", entity_id=external_id,
                     action="create" if before is None else "update",
                     summary=(f"Введена сделка CDS {external_id}" if before is None else f"Изменена сделка CDS {external_id}, версия {trade.version}"),
                     changes=diff(before, after))
        self.s.commit()
        self.value_trade(trade, self.settings.today(), "trade_saved", actor)
        self.s.commit()
        return trade

    def _audit_view(self, v: CdsTradeVersion) -> dict[str, Any]:
        entity = self.s.get(CdsEntity, v.entity_id)
        return {"external_id": v.external_id, "entity": entity.code if entity else None, "side": v.side, "notional": v.notional,
                "coupon_bp": v.coupon_bp, "trade_date": v.trade_date, "effective_date": v.effective_date, "maturity": v.maturity,
                "rebate": v.rebate, "schedule_params": v.schedule_params, "schedule_edited": v.schedule_edited,
                "periods": len(v.schedule.get("rows", [])), "tags": v.tags, "comment": v.comment}

    def set_status(self, trade_id: Any, status: str, actor: Principal) -> CdsTrade:
        trade = self.get(trade_id)
        if trade.status == status:
            return trade
        old, trade.status, trade.updated_by = trade.status, status, actor.username
        audit.record(self.s, actor=actor, entity_type="cds_trade", entity_id=trade.external_id, action=f"status_{status}",
                     summary=f"Сделка CDS {trade.external_id}: {'аннулирована' if status == 'cancelled' else 'восстановлена'}",
                     changes={"status": {"old": old, "new": status}})
        self.s.commit()
        if status == "active":
            self.value_trade(trade, self.settings.today(), "manual", actor)
            self.s.commit()
        return trade

    # ------------------------------------------------------------------ valuation
    def value_trade(self, trade: CdsTrade, d: date, trigger: str, actor: Principal, kind: str | None = None,
                    version: CdsTradeVersion | None = None) -> CdsTradeValuation | None:
        """Value the trade on d and store it; None when it is not alive on d or there is no CDS curve yet."""
        if trade.status != "active":
            return None
        v = version or self.current_version(trade)
        if d < v.trade_date or d > v.maturity:
            return None
        entity = self.s.get(CdsEntity, v.entity_id)
        snap = self.cds.latest_on_or_before(entity, d)
        if snap is None:
            return None
        try:
            out = value_on(v, d, snap, self.cds.config())
        except AppError as exc:
            log.warning("Trade %s not valued on %s: %s", trade.external_id, d, exc.detail)
            return None
        row = CdsTradeValuation(trade_id=trade.id, trade_version=v.version, valuation_date=d,
                                kind=kind or ("eod" if d < self.settings.today() else "intraday"), trigger=trigger,
                                calculated_by=actor.username, curve_date=snap.as_of_date, **out)
        self.s.add(row)
        return row

    def _alive(self, d: date, entity_id: Any | None = None) -> list[tuple[CdsTrade, CdsTradeVersion]]:
        stmt = (select(CdsTrade, CdsTradeVersion).join(CdsTradeVersion, (CdsTradeVersion.trade_id == CdsTrade.id)
                                                       & (CdsTradeVersion.version == CdsTrade.version))
                .where(CdsTrade.status == "active", CdsTradeVersion.trade_date <= d, CdsTradeVersion.maturity >= d))
        if entity_id is not None:
            stmt = stmt.where(CdsTradeVersion.entity_id == entity_id)
        return list(self.s.execute(stmt).all())

    def revalue(self, d: date, trigger: str, actor: Principal, *, entity_code: str | None = None, kind: str | None = None) -> dict[str, Any]:
        entity_id = self.cds.get_entity(entity_code).id if entity_code else None
        done, skipped = [], []
        for trade, v in self._alive(d, entity_id):
            row = self.value_trade(trade, d, trigger, actor, kind=kind, version=v)
            (done if row is not None else skipped).append(trade.external_id)
        self.s.commit()
        return {"date": d.isoformat(), "entity": entity_code, "valued": len(done), "skipped": skipped}

    def end_of_day(self, d: date, actor: Principal) -> dict[str, Any]:
        cal = self.cds.calendar(self.business_calendar_code())
        if not cal.is_business_day(d):
            return {"date": d.isoformat(), "skipped": "нерабочий день"}
        return self.revalue(d, "eod", actor, kind="eod")

    # ------------------------------------------------------------------ reading
    def _latest_valuations(self, trade_ids: list[Any], d: date) -> dict[Any, CdsTradeValuation]:
        if not trade_ids:
            return {}
        sub = (select(CdsTradeValuation.id, func.row_number().over(partition_by=CdsTradeValuation.trade_id,
                                                                   order_by=CdsTradeValuation.calculated_at.desc()).label("rn"))
               .where(CdsTradeValuation.trade_id.in_(trade_ids), CdsTradeValuation.valuation_date == d).subquery())
        rows = self.s.scalars(select(CdsTradeValuation).join(sub, sub.c.id == CdsTradeValuation.id).where(sub.c.rn == 1))
        return {r.trade_id: r for r in rows}

    def trade_tags(self, trade_ids: list[Any]) -> dict[Any, list[str]]:
        out: dict[Any, list[str]] = {}
        if trade_ids:
            for tid, name in self.s.execute(select(CdsTradeTag.trade_id, TradeTag.name).join(TradeTag, TradeTag.id == CdsTradeTag.tag_id)
                                            .where(CdsTradeTag.trade_id.in_(trade_ids)).order_by(TradeTag.name)):
                out.setdefault(tid, []).append(name)
        return out

    def search(self, d: date, *, q: str | None = None, entity_code: str | None = None, tags: list[str] | None = None,
               side: str | None = None, status: str = "active", limit: int = 2000) -> dict[str, Any]:
        stmt = (select(CdsTrade, CdsTradeVersion, CdsEntity)
                .join(CdsTradeVersion, (CdsTradeVersion.trade_id == CdsTrade.id) & (CdsTradeVersion.version == CdsTrade.version))
                .join(CdsEntity, CdsEntity.id == CdsTrade.entity_id))
        if status in ("active", "cancelled"):
            stmt = stmt.where(CdsTrade.status == status)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(CdsTrade.external_id.ilike(like) | CdsTradeVersion.comment.ilike(like))
        if entity_code:
            stmt = stmt.where(CdsEntity.code == entity_code)
        if side in ("buyer", "seller"):
            stmt = stmt.where(CdsTradeVersion.side == side)
        for name in self._clean_tags(tags or []):
            stmt = stmt.where(exists().where(CdsTradeTag.trade_id == CdsTrade.id, CdsTradeTag.tag_id == TradeTag.id,
                                             func.lower(TradeTag.name) == name.lower()))
        rows = self.s.execute(stmt.order_by(CdsTradeVersion.trade_date.desc(), CdsTrade.external_id).limit(limit)).all()
        ids = [t.id for t, _, _ in rows]
        vals = self._latest_valuations(ids, d)
        tag_map = self.trade_tags(ids)
        items = []
        for trade, v, e in rows:
            val = vals.get(trade.id)
            life = "not_started" if d < v.trade_date else "matured" if d > v.maturity else "alive"
            items.append({**self._version_summary(trade, v, e), "tags": tag_map.get(trade.id, []), "life": life,
                          "valuation": self._valuation_out(val) if val else None})
        valued = [i["valuation"] for i in items if i["valuation"]]
        return {"date": d, "items": items, "totals": {"trades": len(items), "valued": len(valued),
                                                      "npv": sum(x["npv"] for x in valued), "cs01": sum(x["cs01"] for x in valued),
                                                      "notional_bought": sum(i["notional"] for i in items if i["side"] == "buyer" and i["life"] == "alive"),
                                                      "notional_sold": sum(i["notional"] for i in items if i["side"] == "seller" and i["life"] == "alive")}}

    def _version_summary(self, trade: CdsTrade, v: CdsTradeVersion, e: CdsEntity) -> dict[str, Any]:
        return {"id": trade.id, "external_id": trade.external_id, "status": trade.status, "version": trade.version,
                "entity_code": e.code, "entity_name": e.name, "side": v.side, "notional": v.notional, "coupon_bp": v.coupon_bp,
                "trade_date": v.trade_date, "effective_date": v.effective_date, "maturity": v.maturity, "rebate": v.rebate,
                "schedule_mode": v.schedule_params.get("mode"), "schedule_edited": v.schedule_edited, "comment": v.comment,
                "updated_at": trade.updated_at, "updated_by": trade.updated_by}

    @staticmethod
    def _valuation_out(r: CdsTradeValuation) -> dict[str, Any]:
        return {"id": r.id, "valuation_date": r.valuation_date, "kind": r.kind, "trigger": r.trigger,
                "trigger_title": VALUATION_TRIGGERS.get(r.trigger, r.trigger), "calculated_at": r.calculated_at,
                "calculated_by": r.calculated_by, "trade_version": r.trade_version, "curve_date": r.curve_date,
                "npv": r.npv, "clean": r.clean, "accrued": r.accrued, "par_spread_bp": r.par_spread_bp, "rpv01": r.rpv01,
                "cs01": r.cs01, "details": r.details}

    def detail(self, trade_id: Any) -> dict[str, Any]:
        trade = self.get(trade_id)
        v = self.current_version(trade)
        e = self.s.get(CdsEntity, v.entity_id)
        versions = list(self.s.scalars(select(CdsTradeVersion).where(CdsTradeVersion.trade_id == trade.id).order_by(CdsTradeVersion.version.desc())))
        all_vals = list(self.s.scalars(select(CdsTradeValuation).where(CdsTradeValuation.trade_id == trade.id)
                                       .order_by(CdsTradeValuation.valuation_date, CdsTradeValuation.calculated_at)))
        per_date: dict[date, CdsTradeValuation] = {}
        for r in all_vals:
            per_date[r.valuation_date] = r  # the latest of each date
        return {
            **self._version_summary(trade, v, e), "tags": self.trade_tags([trade.id]).get(trade.id, []),
            "created_at": trade.created_at, "created_by": trade.created_by, "schedule_params": v.schedule_params,
            "schedule": {**v.schedule, "rebate_amount": -v.notional * v.coupon_bp / 1e4 * float(v.schedule.get("rebate_year_frac") or 0.0),
                         "total_amount": sum(r["amount"] for r in v.schedule["rows"])},
            "versions": [{"version": x.version, "created_by": x.created_by, "created_at": x.created_at, "notional": x.notional,
                          "coupon_bp": x.coupon_bp, "maturity": x.maturity, "side": x.side, "schedule_edited": x.schedule_edited,
                          "tags": x.tags, "comment": x.comment} for x in versions],
            "valuations": [self._valuation_out(r) for r in sorted(per_date.values(), key=lambda r: r.valuation_date)],
            "valuation_records": len(all_vals),
            "latest_valuation": self._valuation_out(all_vals[-1]) if all_vals else None,
        }

    def valuation_records(self, trade_id: Any, d: date | None = None) -> list[dict[str, Any]]:
        trade = self.get(trade_id)
        stmt = select(CdsTradeValuation).where(CdsTradeValuation.trade_id == trade.id)
        if d:
            stmt = stmt.where(CdsTradeValuation.valuation_date == d)
        return [self._valuation_out(r) for r in self.s.scalars(stmt.order_by(CdsTradeValuation.calculated_at.desc()).limit(500))]

    def version_detail(self, trade_id: Any, version: int) -> dict[str, Any]:
        trade = self.get(trade_id)
        v = self.s.scalars(select(CdsTradeVersion).where(CdsTradeVersion.trade_id == trade.id, CdsTradeVersion.version == version)).first()
        if not v:
            raise NotFound(f"Нет версии {version}")
        e = self.s.get(CdsEntity, v.entity_id)
        return {**self._version_summary(trade, v, e), "version": v.version, "schedule_params": v.schedule_params, "schedule": v.schedule,
                "tags": v.tags, "created_by": v.created_by, "created_at": v.created_at}


def enqueue_revaluation(session: Session, entity: CdsEntity, as_of: date | None, trigger: str, actor: Principal) -> None:
    """Best-effort: ask the worker to revalue the entity's live trades (only when it has any)."""
    has = session.scalar(select(exists().where(CdsTrade.entity_id == entity.id, CdsTrade.status == "active")))
    if not has:
        return
    try:
        with session.begin_nested():
            enqueue(session, REVALUE_TASK, {"entity_code": entity.code, "as_of": as_of.isoformat() if as_of else None, "trigger": trigger},
                    trigger=Trigger.SYSTEM, created_by=actor.username)
    except AppError:
        pass  # the worker has not registered the task type yet (first start after an upgrade)
