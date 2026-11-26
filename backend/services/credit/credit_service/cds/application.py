"""Use cases of CDS pricing: calendars, reference entities, CDS curves, contract calculator.

A CDS curve is recalculated automatically (never by hand-made edits):
* a credit curve is saved (current state or a backdated version) -> CDS curves of the reference
  entities priced from it (task credit.cds_recalculate);
* an interest-rate curve is saved in the curves service -> CDS curves whose credit curve discounts
  with it (task credit.cds_rates_changed, the curve's nodes come with the task);
* a reference entity is changed, or the operator presses «Пересчитать».
States move to history every night together with credit curves.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, load_only

from cds_common.audit import TAG_BACKDATED, AuditLog, diff
from cds_common.auth import Principal
from cds_common.errors import AppError, Conflict, NotFound

from .. import settings_store
from ..config import CreditSettings
from ..domain.rates import RateCurve
from ..models import Calendar, CalendarDay, CdsEntity, CdsHistory, CdsState, CurveDefinition, CurveHistory, CurveState, RateSnapshot
from .calendar import BusinessCalendar
from .curve import CurveInputs, build, contract_result
from .isda import (
    PROFILES,
    SBER_OTC,
    SPFI,
    TENOR_MONTHS,
    BracketError,
    DiscountCurve,
    HazardCurve,
    Quote,
    bootstrap,
    build_schedule,
    flat_hazard_for_puf,
    price,
    puf_to_qs_detail,
    standard_maturity,
    with_settings,
    yf_act365f,
)

audit = AuditLog("credit")
log = logging.getLogger(__name__)


def bond_hazard(snapshot_result: dict[str, Any], as_of: date) -> HazardCurve:
    """Monthly piecewise-flat hazard of a credit curve: λ_k (node k, % p.a.) acts on (t_k, t_{k+1}]."""
    nodes = snapshot_result["nodes"]
    times = [yf_act365f(as_of, date.fromisoformat(n["date"])) for n in nodes[1:]]
    lambdas = [float(n["hazard"]) / 100.0 for n in nodes[:-1]]
    return HazardCurve(as_of, times, lambdas)


def discount_from_ref(ref: dict[str, Any], as_of: date) -> tuple[DiscountCurve, list[dict[str, Any]]]:
    """CDS discount curve relative to the valuation date D (nodes of the interest-rate curve after D).

    Log-linear interpolation in the curve's own day count is linear in days, so re-basing the nodes
    to D with DF_D(d) = DF(d)/DF(D) reproduces the interest-rate curve exactly."""
    curve = RateCurve.from_snapshot(ref["code"], date.fromisoformat(str(ref["as_of_date"])), ref.get("day_count", "ACT/365F"), ref["nodes"])
    later = sorted({date.fromisoformat(str(n["date"])) for n in ref["nodes"] if date.fromisoformat(str(n["date"])) > as_of})
    if not later:
        raise AppError("Кривая дисконтирования заканчивается раньше даты оценки", code="cds_no_discount")
    dfs = [float(curve.df_days(as_of, (d - as_of).days)) for d in later]
    return DiscountCurve(as_of, later, dfs), [{"date": d.isoformat(), "df": df} for d, df in zip(later, dfs)]


class CdsApp:
    def __init__(self, session: Session, settings: CreditSettings):
        self.s = session
        self.settings = settings

    # ------------------------------------------------------------------ settings
    def config(self) -> dict[str, Any]:
        return settings_store.load(self.s)["cds"]

    def update_settings(self, data: dict[str, Any], actor: Principal) -> dict[str, Any]:
        cfg = {**self.config(), **data}
        if not 0 <= float(cfg["recovery_quote"]) < 1:
            raise AppError("Rq — в диапазоне [0; 1)", code="invalid_settings")
        if not cfg["tenors"] or any(t not in TENOR_MONTHS for t in cfg["tenors"]):
            raise AppError(f"Сроки — из списка {', '.join(TENOR_MONTHS)}", code="invalid_settings")
        cfg["tenors"] = sorted(set(cfg["tenors"]), key=lambda t: TENOR_MONTHS[t])
        if cfg["day_count"] not in ("ACT/ACT.ISDA", "ACT/365F", "ACT/360"):
            raise AppError("Неизвестный коэффициент дней", code="invalid_settings")
        if cfg["rebate_mode"] not in ("spec", "unadjusted_start", "inclusive_end"):
            raise AppError("Неизвестный режим ребейта", code="invalid_settings")
        for key in ("spfi_calendar", "sber_calendar"):
            if not self.s.get(Calendar, cfg[key]):
                raise AppError(f"Календарь {cfg[key]} не найден", code="invalid_settings")
        settings_store.save(self.s, "cds", cfg, actor)
        self.s.commit()
        return cfg

    def conventions(self, profile: str = "SPFI_STD") -> tuple[Any, BusinessCalendar]:
        cfg = self.config()
        if profile == "SBER_OTC":
            conv = with_settings(SBER_OTC, day_count=cfg["day_count"], protection_from_effective=cfg["protection_from_effective"],
                                 adjust_period_ends=cfg["sber_adjust_period_ends"], last_period_inclusive=cfg["sber_last_period_inclusive"])
            return conv, self.calendar(cfg["sber_calendar"])
        conv = with_settings(SPFI, day_count=cfg["day_count"], protection_from_effective=cfg["protection_from_effective"],
                             rebate_mode=cfg["rebate_mode"])
        return conv, self.calendar(cfg["spfi_calendar"])

    # ------------------------------------------------------------------ calendars
    def list_calendars(self) -> list[dict[str, Any]]:
        counts = dict(self.s.execute(select(CalendarDay.calendar_code, func.count()).group_by(CalendarDay.calendar_code)).all())
        return [{"code": c.code, "name": c.name, "base_code": c.base_code, "description": c.description, "exceptions": counts.get(c.code, 0)}
                for c in self.s.scalars(select(Calendar).order_by(Calendar.code))]

    def calendar(self, code: str) -> BusinessCalendar:
        cal = self.s.get(Calendar, code)
        if not cal:
            raise NotFound(f"Календарь {code} не найден")
        days = {d.day: d.is_business for d in self.s.scalars(select(CalendarDay).where(CalendarDay.calendar_code == code))}
        return BusinessCalendar(code, days, self.calendar(cal.base_code) if cal.base_code else None)

    def calendar_days(self, code: str, year: int) -> list[CalendarDay]:
        if not self.s.get(Calendar, code):
            raise NotFound(f"Календарь {code} не найден")
        return list(self.s.scalars(select(CalendarDay).where(
            CalendarDay.calendar_code == code, CalendarDay.day >= date(year, 1, 1), CalendarDay.day <= date(year, 12, 31)
        ).order_by(CalendarDay.day)))

    def set_day(self, code: str, day: date, is_business: bool, comment: str, actor: Principal) -> None:
        if not self.s.get(Calendar, code):
            raise NotFound(f"Календарь {code} не найден")
        row = self.s.get(CalendarDay, (code, day))
        before = {"is_business": row.is_business, "comment": row.comment} if row else None
        if row is None:
            self.s.add(CalendarDay(calendar_code=code, day=day, is_business=is_business, comment=comment, updated_by=actor.username))
        else:
            row.is_business, row.comment, row.updated_by = is_business, comment, actor.username
        audit.record(self.s, actor=actor, entity_type="calendar", entity_id=code, action="day_set",
                     summary=f"Календарь {code}: {day:%d.%m.%Y} — {'рабочий' if is_business else 'нерабочий'} день",
                     changes={"day": day, **diff(before, {"is_business": is_business, "comment": comment})})
        self.s.commit()

    def delete_day(self, code: str, day: date, actor: Principal) -> None:
        row = self.s.get(CalendarDay, (code, day))
        if not row:
            raise NotFound("Исключение не найдено")
        self.s.delete(row)
        audit.record(self.s, actor=actor, entity_type="calendar", entity_id=code, action="day_delete",
                     summary=f"Календарь {code}: исключение {day:%d.%m.%Y} удалено", changes={"day": day})
        self.s.commit()

    # ------------------------------------------------------------------ reference entities
    def list_entities(self) -> list[CdsEntity]:
        return list(self.s.scalars(select(CdsEntity).order_by(CdsEntity.code)))

    def get_entity(self, code: str, *, lock: bool = False) -> CdsEntity:
        stmt = select(CdsEntity).where(CdsEntity.code == code)
        if lock:
            stmt = stmt.with_for_update()
        e = self.s.scalars(stmt).first()
        if not e:
            raise NotFound(f"Контрольное лицо {code} не найдено")
        return e

    def _entity_values(self, data: dict[str, Any]) -> dict[str, Any]:
        curve = self.s.scalars(select(CurveDefinition).where(CurveDefinition.code == data.pop("credit_curve_code"))).first()
        if not curve:
            raise AppError("Кредитная кривая не найдена", code="unknown_curve")
        if data["max_tenor"] not in TENOR_MONTHS:
            raise AppError("Неизвестный максимальный срок", code="invalid_entity")
        basis = {k: float(v) for k, v in (data.get("basis_bp") or {}).items() if k in TENOR_MONTHS and v not in (None, "")}
        return {**data, "credit_curve_id": curve.id, "basis_bp": basis}

    def _audit_view(self, values: dict[str, Any]) -> dict[str, Any]:
        out = dict(values)
        if "credit_curve_id" in out:
            curve = self.s.get(CurveDefinition, out.pop("credit_curve_id"))
            out["credit_curve"] = curve.code if curve else None
        return out

    def save_entity(self, code: str | None, data: dict[str, Any], actor: Principal) -> CdsEntity:
        values = self._entity_values(dict(data))
        if code is None:
            if self.s.scalars(select(CdsEntity).where(CdsEntity.code == values["code"])).first():
                raise Conflict(f"Контрольное лицо {values['code']} уже есть")
            entity = CdsEntity(**values)
            self.s.add(entity)
            self.s.flush()
            audit.record(self.s, actor=actor, entity_type="cds_entity", entity_id=entity.code, action="create",
                         summary=f"Добавлено контрольное лицо CDS {entity.code}", changes=diff(None, self._audit_view(values)))
        else:
            entity = self.get_entity(code, lock=True)
            values.pop("code", None)
            before = {k: getattr(entity, k) for k in values}
            changes = diff(self._audit_view(before), self._audit_view(values))
            for k, v in values.items():
                setattr(entity, k, v)
            if changes:
                audit.record(self.s, actor=actor, entity_type="cds_entity", entity_id=entity.code, action="update",
                             summary=f"Изменено контрольное лицо CDS {entity.code}", changes=changes)
        self.s.commit()
        return entity

    def credit_curve_codes(self) -> dict[Any, str]:
        return dict(self.s.execute(select(CurveDefinition.id, CurveDefinition.code)).all())

    # ------------------------------------------------------------------ calculation
    def _credit_snapshot(self, entity: CdsEntity, as_of: date | None) -> tuple[CurveDefinition, CurveState | CurveHistory]:
        defn = self.s.get(CurveDefinition, entity.credit_curve_id)
        state = self.s.get(CurveState, defn.id)
        if as_of is None or (state is not None and state.as_of_date == as_of):
            if state is None:
                raise AppError(f"Кредитная кривая {defn.code} ещё не построена", code="cds_no_credit_curve")
            return defn, state
        hist = self.s.scalars(select(CurveHistory).where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date == as_of)
                              .order_by(CurveHistory.version.desc()).limit(1)).first()
        if hist is None:
            raise AppError(f"Нет кредитной кривой {defn.code} на {as_of}", code="cds_no_credit_curve")
        return defn, hist

    def _discount_ref(self, defn: CurveDefinition, snap: CurveState | CurveHistory, override: dict[str, Any] | None) -> dict[str, Any]:
        if override:
            return override
        ref = (snap.inputs or {}).get("discount") or {}
        if ref.get("nodes"):
            return ref
        cached = self.s.scalars(select(RateSnapshot).where(RateSnapshot.code == defn.discount_curve_code,
                                                           RateSnapshot.as_of_date <= snap.as_of_date)
                                .order_by(RateSnapshot.as_of_date.desc()).limit(1)).first()
        if cached is None:
            raise AppError(f"Нет узлов кривой дисконтирования {defn.discount_curve_code}: пересохраните кредитную или процентную кривую",
                           code="cds_no_discount")
        return {"code": cached.code, "as_of_date": cached.as_of_date.isoformat(), "kind": cached.kind, "version": cached.version,
                "day_count": cached.day_count, "nodes": cached.nodes}

    def compute(self, entity: CdsEntity, as_of: date | None = None, rates: dict[str, Any] | None = None) -> tuple[dict, dict]:
        defn, snap = self._credit_snapshot(entity, as_of)
        valuation = snap.as_of_date
        cfg = self.config()
        ref = self._discount_ref(defn, snap, rates)
        dc, dc_nodes = discount_from_ref(ref, valuation)
        recovery = entity.recovery_override if entity.recovery_override is not None else float(snap.result["params"]["recovery_rate"])
        conv, cal = self.conventions("SPFI_STD")
        max_m = TENOR_MONTHS[entity.max_tenor]
        tenors = [t for t in cfg["tenors"] if TENOR_MONTHS[t] <= max_m]
        coupons = sorted({c / 1e4 for c in cfg["coupons_bp"]} | {entity.standard_coupon_bp / 1e4})
        try:
            result = build(CurveInputs(valuation, bond_hazard(snap.result, valuation), dc, recovery, float(cfg["recovery_quote"]), tenors,
                                       entity.standard_coupon_bp / 1e4, coupons, entity.basis_bp or {}, conv, cal))
        except (ValueError, BracketError) as exc:
            raise AppError(f"CDS-кривая {entity.code}: {exc}", code="cds_failed") from None
        inputs = {
            "credit_curve": {"code": defn.code, "as_of_date": valuation.isoformat(), "kind": "state" if isinstance(snap, CurveState) else "history",
                             "version": getattr(snap, "version", None), "recovery_rate": float(snap.result["params"]["recovery_rate"]),
                             "nodes": [{"date": n["date"], "hazard": n["hazard"], "survival": n["survival"]} for n in snap.result["nodes"]]},
            "discount": {k: ref.get(k) for k in ("code", "as_of_date", "kind", "version", "day_count")} | {"nodes": dc_nodes},
            "recovery": recovery, "recovery_overridden": entity.recovery_override is not None,
            "standard_coupon_bp": entity.standard_coupon_bp, "basis_bp": entity.basis_bp or {}, "doc_basis_bp": entity.doc_basis_bp,
            "settings": cfg, "conventions": {"name": conv.name, "day_count": conv.day_count, "rebate_mode": conv.rebate_mode,
                                             "protection_from_effective": conv.protection_from_effective, "calendar": cal.code},
        }
        return inputs, result

    def _history_exists(self, entity_id: Any, as_of: date) -> bool:
        return bool(self.s.scalar(select(func.count()).select_from(CdsHistory).where(CdsHistory.entity_id == entity_id, CdsHistory.as_of_date == as_of)))

    def _archive(self, entity: CdsEntity, state: CdsState, source: str, actor: Principal) -> None:
        self.s.add(CdsHistory(entity_id=entity.id, as_of_date=state.as_of_date, version=1, source=source, inputs=state.inputs,
                              result=state.result, trigger=state.trigger, created_by=state.calculated_by))
        audit.record(self.s, actor=actor, entity_type="cds_curve", entity_id=entity.code, action="history_archive",
                     summary=f"CDS-кривая {entity.code} на {state.as_of_date} перенесена в историю ({source})",
                     changes={"as_of_date": state.as_of_date, "source": source})

    def _summary(self, result: dict[str, Any]) -> dict[str, Any]:
        return {r["tenor"]: round(r["par_spread_bp"], 2) for r in result["table"]}

    def save_state(self, entity: CdsEntity, inputs: dict, result: dict, trigger: str, actor: Principal) -> CdsState:
        as_of = date.fromisoformat(result["valuation_date"])
        state = self.s.get(CdsState, entity.id)
        if state is not None and state.as_of_date > as_of:
            raise Conflict(f"CDS-кривая {entity.code} уже рассчитана на более позднюю дату {state.as_of_date}", code="state_newer")
        if state is not None and state.as_of_date < as_of and not self._history_exists(entity.id, state.as_of_date):
            self._archive(entity, state, "auto_archive", actor)
        before = self._summary(state.result) if state else None
        if state is None:
            state = CdsState(entity_id=entity.id, as_of_date=as_of, inputs=inputs, result=result, trigger=trigger, calculated_by=actor.username)
            self.s.add(state)
        else:
            state.as_of_date, state.inputs, state.result, state.trigger = as_of, inputs, result, trigger
            state.calculated_by, state.calculated_at = actor.username, datetime.now(timezone.utc)
        audit.record(self.s, actor=actor, entity_type="cds_curve", entity_id=entity.code, action="state_save",
                     summary=f"Рассчитана CDS-кривая {entity.code} на {as_of} ({TRIGGERS.get(trigger, trigger)})",
                     changes={"par_spread_bp": {"old": before, "new": self._summary(result)}})
        self.s.flush()
        from .trades import enqueue_revaluation  # trades depend on this module

        enqueue_revaluation(self.s, entity, None, "cds_curve", actor)
        return state

    def save_history(self, entity: CdsEntity, inputs: dict, result: dict, trigger: str, actor: Principal) -> CdsHistory:
        as_of = date.fromisoformat(result["valuation_date"])
        latest = self.s.scalar(select(func.max(CdsHistory.version)).where(CdsHistory.entity_id == entity.id, CdsHistory.as_of_date == as_of)) or 0
        row = CdsHistory(entity_id=entity.id, as_of_date=as_of, version=latest + 1, source="backdated", inputs=inputs, result=result,
                         trigger=trigger, created_by=actor.username)
        self.s.add(row)
        audit.record(self.s, actor=actor, entity_type="cds_curve", entity_id=entity.code, action="history_edit",
                     summary=f"Изменение задним числом: CDS-кривая {entity.code} на {as_of}, версия {latest + 1}",
                     changes={"par_spread_bp": self._summary(result)}, tags=[TAG_BACKDATED])
        self.s.flush()
        from .trades import enqueue_revaluation  # trades depend on this module

        enqueue_revaluation(self.s, entity, as_of, "backdated", actor)
        return row

    def recalc_entity(self, code: str, actor: Principal, trigger: str = "manual", rates: dict[str, Any] | None = None) -> CdsState:
        entity = self.get_entity(code, lock=True)
        inputs, result = self.compute(entity, rates=rates)
        state = self.save_state(entity, inputs, result, trigger, actor)
        self.s.commit()
        return state

    def recalc_for_credit_curve(self, curve_code: str, scope: str, as_of: date | None, actor: Principal) -> dict[str, Any]:
        defn = self.s.scalars(select(CurveDefinition).where(CurveDefinition.code == curve_code)).first()
        if defn is None:
            return {"skipped": "unknown credit curve"}
        report: dict[str, Any] = {"curve": curve_code, "scope": scope, "done": [], "errors": []}
        entities = self.s.scalars(select(CdsEntity).where(CdsEntity.credit_curve_id == defn.id, CdsEntity.is_active.is_(True))
                                  .with_for_update()).all()
        for entity in entities:
            try:
                with self.s.begin_nested():
                    if scope == "history":
                        inputs, result = self.compute(entity, as_of=as_of)
                        self.save_history(entity, inputs, result, "credit_curve", actor)
                    else:
                        inputs, result = self.compute(entity)
                        self.save_state(entity, inputs, result, "credit_curve", actor)
                report["done"].append(entity.code)
            except AppError as exc:
                report["errors"].append({"entity": entity.code, "error": exc.detail})
        self.s.commit()
        return report

    def on_rates(self, snap: dict[str, Any], actor: Principal) -> dict[str, Any]:
        as_of = date.fromisoformat(str(snap["as_of_date"]))
        values = {"kind": snap.get("kind", "state"), "version": snap.get("version"), "day_count": snap.get("day_count", "ACT/365F"),
                  "nodes": snap["nodes"]}
        self.s.execute(pg_insert(RateSnapshot).values(code=snap["code"], as_of_date=as_of, **values)
                       .on_conflict_do_update(index_elements=["code", "as_of_date"], set_={**values, "received_at": func.now()}))
        report: dict[str, Any] = {"curve": snap["code"], "as_of_date": as_of.isoformat(), "done": [], "skipped": [], "errors": []}
        if values["kind"] != "state":
            self.s.commit()
            return report  # backdated rate edits do not rewrite CDS history automatically
        rows = self.s.execute(
            select(CdsEntity, CurveState).join(CurveDefinition, CurveDefinition.id == CdsEntity.credit_curve_id)
            .join(CurveState, CurveState.curve_id == CurveDefinition.id)
            .where(CdsEntity.is_active.is_(True), CurveDefinition.discount_curve_code == snap["code"])
        ).all()
        ref = {"code": snap["code"], "as_of_date": as_of.isoformat(), **values}
        for entity, credit_state in rows:
            if credit_state.as_of_date < as_of:
                report["skipped"].append(entity.code)  # the new curve is later than the credit curve
                continue
            try:
                with self.s.begin_nested():
                    inputs, result = self.compute(entity, rates=ref)
                    self.save_state(entity, inputs, result, "rates", actor)
                report["done"].append(entity.code)
            except AppError as exc:
                report["errors"].append({"entity": entity.code, "error": exc.detail})
        self.s.commit()
        return report

    def archive_states(self, up_to: date, actor: Principal) -> dict[str, Any]:
        archived = []
        for entity, state in self.s.execute(select(CdsEntity, CdsState).join(CdsState, CdsState.entity_id == CdsEntity.id)
                                            .with_for_update(of=CdsEntity)).all():
            if state.as_of_date <= up_to and not self._history_exists(entity.id, state.as_of_date):
                self._archive(entity, state, "snapshot", actor)
                archived.append({"entity": entity.code, "as_of_date": state.as_of_date.isoformat()})
        self.s.commit()
        return {"cds_archived": archived}

    # ------------------------------------------------------------------ reading
    def state_infos(self) -> dict[Any, CdsState]:
        return {s.entity_id: s for s in self.s.scalars(select(CdsState))}

    def get_state(self, code: str) -> tuple[CdsEntity, CdsState]:
        entity = self.get_entity(code)
        state = self.s.get(CdsState, entity.id)
        if not state:
            raise NotFound(f"CDS-кривая {code} ещё не рассчитана")
        return entity, state

    def list_history(self, code: str) -> list[dict[str, Any]]:
        entity = self.get_entity(code)
        out: dict[date, dict[str, Any]] = {}
        for row in self.s.scalars(select(CdsHistory).options(load_only(CdsHistory.as_of_date, CdsHistory.version, CdsHistory.source,
                                                                      CdsHistory.created_at, CdsHistory.created_by))
                                  .where(CdsHistory.entity_id == entity.id)
                                  .order_by(CdsHistory.as_of_date.desc(), CdsHistory.version.desc())):
            item = out.get(row.as_of_date)
            if item is None:
                out[row.as_of_date] = {"as_of_date": row.as_of_date, "latest_version": row.version, "versions": 1, "source": row.source,
                                       "author": row.created_by, "saved_at": row.created_at, "edited": row.version > 1 or row.source == "backdated"}
            else:
                item["versions"] += 1
        return list(out.values())

    def get_history(self, code: str, as_of: date, version: int | None) -> tuple[CdsEntity, CdsHistory]:
        entity = self.get_entity(code)
        stmt = select(CdsHistory).where(CdsHistory.entity_id == entity.id, CdsHistory.as_of_date == as_of)
        stmt = stmt.where(CdsHistory.version == version) if version else stmt.order_by(CdsHistory.version.desc())
        row = self.s.scalars(stmt.limit(1)).first()
        if not row:
            raise NotFound(f"Нет CDS-кривой {code} на {as_of}")
        return entity, row

    def snapshot_on(self, entity: CdsEntity, as_of: date) -> CdsState | CdsHistory | None:
        """Latest calculation for the date: the state if it is for that date, else the latest history version."""
        state = self.s.get(CdsState, entity.id)
        if state is not None and state.as_of_date == as_of:
            return state
        return self.s.scalars(select(CdsHistory).where(CdsHistory.entity_id == entity.id, CdsHistory.as_of_date == as_of)
                              .order_by(CdsHistory.version.desc()).limit(1)).first()

    # ------------------------------------------------------------------ contract calculator
    def _contract_context(self, req: dict[str, Any]) -> dict[str, Any]:
        """Curve snapshot, conventions, discount curve and schedule of the contract described by the request."""
        entity = self.get_entity(req["entity_code"])
        snap: CdsState | CdsHistory
        if req.get("as_of_date"):
            snap = self.snapshot_on(entity, req["as_of_date"])
            if snap is None:
                raise NotFound(f"Нет CDS-кривой {entity.code} на {req['as_of_date']}")
        else:
            _, snap = self.get_state(entity.code)
        valuation = snap.as_of_date
        profile = req.get("profile") or "SPFI_STD"
        if profile not in PROFILES:
            raise AppError("Неизвестный профиль конвенций", code="invalid_contract")
        conv, cal = self.conventions(profile)
        nodes = snap.inputs["discount"]["nodes"]
        dc = DiscountCurve(valuation, [date.fromisoformat(n["date"]) for n in nodes], [float(n["df"]) for n in nodes])
        trade_date = req.get("trade_date") or valuation
        if trade_date < valuation:
            raise AppError("Дата сделки не может быть раньше даты кривой", code="invalid_contract")
        if req.get("maturity"):
            maturity = req["maturity"]
        else:
            eff = cal.add_business_days(trade_date, conv.effective_lag_bd) if conv.effective_lag_bd else trade_date
            maturity = standard_maturity(eff, req.get("tenor") or "5Y")
        if maturity <= cal.add_business_days(trade_date, 2):
            raise AppError("Плановая дата прекращения — не раньше T + 3 рабочих дней", code="invalid_contract")
        coupon = float(req["coupon_bp"]) / 1e4 if req.get("coupon_bp") is not None else entity.standard_coupon_bp / 1e4
        return {
            "entity": entity, "snap": snap, "res": snap.result, "inputs": snap.inputs, "valuation": valuation, "profile": profile,
            "conv": conv, "cal": cal, "dc": dc, "trade_date": trade_date, "maturity": maturity, "coupon": coupon,
            "notional": float(req.get("notional") or 1e8), "sched": build_schedule(trade_date, maturity, conv, cal),
        }

    @staticmethod
    def _convert(ctx: dict[str, Any], *, qs: float | None = None, puf: float | None = None) -> tuple[float, float, float]:
        """ISDA quote convention: (QS, PUF, flat λ) of the flat hazard curve with recovery Rq that prices the quote.
        QS → the flat curve whose par spread is QS → PUF at the contract coupon; PUF → the flat curve with that PUF → QS."""
        sched, val, cpn, conv, dc = ctx["sched"], ctx["valuation"], ctx["coupon"], ctx["conv"], ctx["dc"]
        rq = float(ctx["res"]["recovery_quote"])
        try:
            if qs is not None:
                lam = flat_hazard_for_puf(sched, val, qs, rq, conv, dc, 0.0)
                puf = price(sched, val, cpn, rq, conv, dc, HazardCurve.flat(val, lam))["puf"]
            elif puf is not None:
                qs, lam = puf_to_qs_detail(sched, val, cpn, puf, conv, dc, rq)
            else:
                raise AppError("Задайте QS или upfront (PUF)", code="invalid_quote")
        except BracketError:
            raise AppError("Котировка вне допустимого диапазона: плоская кривая с такой ценой требует λ ≤ 0 или λ > 500%",
                           code="invalid_quote") from None
        return qs, puf, lam

    @staticmethod
    def _quote_source(req: dict[str, Any]) -> str | None:
        src = req.get("quote_input")
        if src == "qs" and req.get("qs_bp") is not None or src == "puf" and req.get("upfront_pct") is not None:
            return src
        return "puf" if req.get("upfront_pct") is not None else "qs" if req.get("qs_bp") is not None else None

    def convert_quote(self, req: dict[str, Any]) -> dict[str, Any]:
        """QS ↔ upfront (PUF) of the contract by the ISDA convention (flat hazard, Rq); needs only the discount curve."""
        ctx = self._contract_context(req)
        src = self._quote_source(req)
        if src is None:
            raise AppError("Задайте QS или upfront (PUF)", code="invalid_quote")
        qs, puf, lam = self._convert(ctx, **({"qs": float(req["qs_bp"]) / 1e4} if src == "qs" else {"puf": float(req["upfront_pct"]) / 100}))
        sched, cpn, n = ctx["sched"], ctx["coupon"], ctx["notional"]
        return {
            "quote_input": src, "qs_bp": qs * 1e4, "upfront_pct": puf * 100, "flat_lambda": lam,
            "recovery_quote": float(ctx["res"]["recovery_quote"]), "coupon_bp": cpn * 1e4, "curve_date": ctx["valuation"].isoformat(),
            "maturity": sched.maturity.isoformat(), "effective_date": sched.effective_date.isoformat(),
            "upfront_settle_date": sched.upfront_settle_date.isoformat(), "rebate_year_frac": sched.rebate_year_frac,
            "upfront_amount": puf * n, "rebate_amount": -cpn * n * sched.rebate_year_frac,
            "initial_payment": (puf - cpn * sched.rebate_year_frac) * n,
        }

    def calculate_contract(self, req: dict[str, Any]) -> dict[str, Any]:
        ctx = self._contract_context(req)
        entity, res, inputs, valuation, profile = ctx["entity"], ctx["res"], ctx["inputs"], ctx["valuation"], ctx["profile"]
        conv, cal, dc, trade_date, sched = ctx["conv"], ctx["cal"], ctx["dc"], ctx["trade_date"], ctx["sched"]
        notional, coupon = ctx["notional"], ctx["coupon"]
        recovery = float(req["recovery"]) if req.get("recovery") is not None else float(res["recovery"])
        hc = HazardCurve(valuation, [h["t"] for h in res["hazard"]], [h["lambda"] for h in res["hazard"]])
        warnings: list[str] = []
        doc_basis = float(entity.doc_basis_bp or 0.0) if profile == "SBER_OTC" else 0.0
        if doc_basis or req.get("recovery") is not None:
            # Sber documentation basis / another recovery: re-bootstrap from the curve's par spreads at standard maturities
            spfi, spfi_cal = self.conventions("SPFI_STD")
            quotes = [Quote(date.fromisoformat(r["maturity"]), "PAR", r["par_spread"] + doc_basis / 1e4) for r in res["table"] if not r["dropped"]]
            try:
                hc = bootstrap(valuation, valuation, quotes, recovery, spfi, spfi_cal, dc)
            except BracketError as exc:
                raise AppError(f"Не удалось перестроить кривую: {exc}", code="cds_failed") from None
            if doc_basis:
                warnings.append(f"Кривая перестроена с документационным базисом Сбера {doc_basis:+.1f} б.п.")
        sign = 1.0 if (req.get("side") or "buyer") == "buyer" else -1.0
        out = contract_result(sched, valuation, hc, dc, conv, recovery, float(res["recovery_quote"]), coupon, [coupon])
        puf_model = out["puf"][f"{round(coupon * 1e4)}"]
        pv_dirty = out["protection"] - coupon * out["rpv01_dirty"]
        # the trade quote: upfront (PUF) or QS, converted into each other by the ISDA convention
        src = self._quote_source(req)
        upfront, trade_qs, trade_lam = None, None, None
        if src == "qs":
            trade_qs, puf_trade, trade_lam = self._convert(ctx, qs=float(req["qs_bp"]) / 1e4)
            upfront = puf_trade * 100
        elif src == "puf":
            upfront = float(req["upfront_pct"])
            trade_qs, _, trade_lam = self._convert(ctx, puf=upfront / 100)
        npv = sign * notional * pv_dirty
        initial_model = (puf_model - coupon * sched.rebate_year_frac) * notional
        initial_trade = None
        if upfront is not None:
            initial_trade = (upfront / 100.0 - coupon * sched.rebate_year_frac) * notional
            npv = sign * notional * out["df_settle"] * (puf_model - upfront / 100.0)
        for row in out["schedule"]:
            row["amount"] = notional * row["coupon"]
            row["pv_amount"] = notional * row["pv_coupon"] if row["pv_coupon"] is not None else None
        if profile == "SPFI_STD" and abs(coupon * 1e4 - entity.standard_coupon_bp) > 1e-9:
            warnings.append(f"Купон отличается от стандартного спреда контрольного лица ({entity.standard_coupon_bp} б.п.)")
        return {
            **out,
            "entity_code": entity.code, "curve_date": valuation.isoformat(), "profile": profile, "trade_date": trade_date.isoformat(),
            "coupon_bp": coupon * 1e4, "notional": notional, "side": "buyer" if sign > 0 else "seller", "recovery": recovery,
            "puf_model": puf_model, "par_spread_bp": out["par_spread"] * 1e4, "qs_bp": out["qs"] * 1e4,
            "rebate_amount": -coupon * notional * sched.rebate_year_frac,
            "initial_payment_model": initial_model, "initial_payment_trade": initial_trade,
            "pv_dirty": pv_dirty, "npv": npv, "upfront_pct": upfront, "pd": 1.0 - out["survival"],
            "quote_input": src, "trade_qs_bp": trade_qs * 1e4 if trade_qs is not None else None, "trade_flat_lambda": trade_lam,
            "recovery_quote": float(res["recovery_quote"]),
            "jtd_buyer": notional * (1.0 - recovery), "doc_basis_bp": doc_basis, "warnings": warnings,
            "hazard": [{"t": t, "date": (valuation + timedelta(days=round(t * 365))).isoformat(), "lambda": lam}
                       for t, lam in zip(hc.times, hc.lambdas)],
            "discount_nodes": inputs["discount"]["nodes"], "conventions": {"name": conv.name, "day_count": conv.day_count,
                                                                            "calendar": cal.code, "rebate_mode": conv.rebate_mode},
        }

    # ------------------------------------------------------------------ daily file for position keeping
    def latest_on_or_before(self, entity: CdsEntity, as_of: date) -> CdsState | CdsHistory | None:
        state = self.s.get(CdsState, entity.id)
        hist = self.s.scalars(select(CdsHistory).where(CdsHistory.entity_id == entity.id, CdsHistory.as_of_date <= as_of)
                              .order_by(CdsHistory.as_of_date.desc(), CdsHistory.version.desc()).limit(1)).first()
        if state is not None and state.as_of_date <= as_of and (hist is None or state.as_of_date >= hist.as_of_date):
            return state
        return hist

    def daily(self, as_of: date) -> list[tuple[CdsEntity, CdsState | CdsHistory]]:
        """For every active entity: the curve of the date, or the latest earlier one (its date is in valuation_date)."""
        out = []
        for entity in self.list_entities():
            if not entity.is_active:
                continue
            snap = self.latest_on_or_before(entity, as_of)
            if snap is not None:
                out.append((entity, snap))
        if not out:
            raise NotFound(f"Нет CDS-кривых на {as_of}")
        return out


TRIGGERS = {"credit_curve": "сохранена кредитная кривая", "rates": "сохранена процентная кривая",
            "entity": "изменено контрольное лицо", "manual": "пересчёт вручную"}
