"""Use cases of the curves service.

Lifecycle of a curve:
  * intraday the operator builds the curve and saves it as the CURRENT STATE
    (one row per curve, overwritten);
  * every night the scheduled task moves states of past dates into HISTORY (version 1);
  * a change of history never overwrites: it adds version N+1 and is journaled with
    the tag "backdated" («изменение задним числом»).
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

import numpy as np
from dateutil.relativedelta import relativedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from cds_common.audit import TAG_BACKDATED, AuditLog, diff
from cds_common.auth import Principal
from cds_common.errors import AppError, Conflict, NotFound

from .config import CurvesSettings
from .domain.calibration import CalibrationParams, CalibrationResult, calibrate, par_curve
from .domain.conventions import DayCount, SwapConventions
from .domain.curve import Compounding, DiscountCurve
from .domain.quotes import InputMode, QuoteError, parse_quotes
from .models import Currency, CurveDefinition, CurveHistory, CurveState
from .schemas import (
    CurrencyIn,
    CurrencyUpdate,
    DefinitionBase,
    DefinitionIn,
    EvaluateRequest,
    QuotesPayload,
)

audit = AuditLog("curves")

_MODE_FIELDS = {InputMode.BID_ASK: ("bid", "ask"), InputMode.MID_SPREAD: ("mid", "spread")}


def conventions_of(defn: CurveDefinition) -> SwapConventions:
    return SwapConventions(DayCount(defn.day_count), defn.fixed_frequency_months, defn.single_payment_max_months)


def effective_params(defn: CurveDefinition, overrides: dict[str, Any] | None = None) -> CalibrationParams:
    return CalibrationParams().with_overrides(defn.default_params).with_overrides(overrides)


def with_linear_par(result: dict[str, Any]) -> dict[str, Any]:
    """Par curve of a stored result = adjusted mids linearly interpolated between tenors.

    Applied on read, so curves saved before this rule are shown the same way as new ones
    without rewriting immutable history (par is derived presentation data, not an input).
    """
    fitted, nodes = result.get("fitted") or [], result.get("nodes") or []
    if not fitted or not nodes:
        return result
    values = par_curve(
        np.array([n["t"] for n in nodes], dtype=float),
        np.array([f["t"] for f in fitted], dtype=float),
        np.array([f["model"] for f in fitted], dtype=float),
    )
    return {**result, "nodes": [{**n, "par": v} for n, v in zip(nodes, values)]}


def result_to_dict(res: CalibrationResult, conv: SwapConventions) -> dict[str, Any]:
    def ser(obj: Any) -> Any:
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {k: ser(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [ser(v) for v in obj]
        return obj

    return {
        "as_of_date": res.as_of.isoformat(),
        "params": res.params.to_dict(),
        "conventions": {
            "day_count": conv.day_count.value,
            "fixed_frequency_months": conv.fixed_frequency_months,
            "single_payment_max_months": conv.single_payment_max_months,
        },
        "nodes": [ser(asdict(n)) for n in res.nodes],
        "fitted": [ser(asdict(f)) for f in res.fitted],
        "stats": res.stats,
        "warnings": res.warnings,
    }


def _clean_quotes(rows: list[dict[str, Any]], mode: InputMode) -> list[dict[str, Any]]:
    keys = ("tenor", *_MODE_FIELDS[mode])
    return [{k: row.get(k) for k in keys} for row in rows]


class CurvesApp:
    def __init__(self, session: Session, settings: CurvesSettings):
        self.s = session
        self.settings = settings

    # ------------------------------------------------------------------ currencies
    def list_currencies(self) -> list[Currency]:
        return list(self.s.scalars(select(Currency).order_by(Currency.code)))

    def create_currency(self, data: CurrencyIn, actor: Principal) -> Currency:
        if self.s.get(Currency, data.code):
            raise Conflict(f"Валюта {data.code} уже существует")
        cur = Currency(**data.model_dump())
        self.s.add(cur)
        audit.record(
            self.s, actor=actor, entity_type="currency", entity_id=data.code, action="create",
            summary=f"Добавлена валюта {data.code}", changes=diff(None, data.model_dump()),
        )
        self.s.commit()
        return cur

    def update_currency(self, code: str, data: CurrencyUpdate, actor: Principal) -> Currency:
        cur = self.s.get(Currency, code)
        if not cur:
            raise NotFound(f"Валюта {code} не найдена")
        before = {"name": cur.name, "numeric_code": cur.numeric_code, "is_active": cur.is_active}
        changes = diff(before, data.model_dump())
        for k, v in data.model_dump().items():
            setattr(cur, k, v)
        if changes:
            audit.record(
                self.s, actor=actor, entity_type="currency", entity_id=code, action="update",
                summary=f"Изменена валюта {code}", changes=changes,
            )
        self.s.commit()
        return cur

    # ------------------------------------------------------------------ definitions
    def list_definitions(self) -> list[CurveDefinition]:
        return list(self.s.scalars(select(CurveDefinition).order_by(CurveDefinition.code)))

    def get_definition(self, code: str, *, lock: bool = False) -> CurveDefinition:
        stmt = select(CurveDefinition).where(CurveDefinition.code == code)
        if lock:
            stmt = stmt.with_for_update()
        defn = self.s.scalars(stmt).first()
        if not defn:
            raise NotFound(f"Кривая {code} не найдена")
        return defn

    def state_infos(self) -> dict[Any, CurveState]:
        rows = self.s.scalars(
            select(CurveState).options(load_only(CurveState.curve_id, CurveState.as_of_date, CurveState.saved_by, CurveState.saved_at))
        )
        return {r.curve_id: r for r in rows}

    def last_history_dates(self) -> dict[Any, date]:
        rows = self.s.execute(select(CurveHistory.curve_id, func.max(CurveHistory.as_of_date)).group_by(CurveHistory.curve_id))
        return {cid: d for cid, d in rows}

    def _definition_dict(self, data: DefinitionBase) -> dict[str, Any]:
        d = data.model_dump()
        d["default_params"] = data.default_params.overrides()
        return d

    def _validate_params(self, overrides: dict[str, Any]) -> None:
        try:
            CalibrationParams().with_overrides(overrides).validate()
        except ValueError as exc:
            raise AppError(str(exc), code="invalid_params") from None

    def create_definition(self, data: DefinitionIn, actor: Principal) -> CurveDefinition:
        if self.s.scalars(select(CurveDefinition).where(CurveDefinition.code == data.code)).first():
            raise Conflict(f"Кривая с кодом {data.code} уже существует")
        if not self.s.get(Currency, data.currency_code):
            raise AppError(f"Валюта {data.currency_code} не найдена", code="unknown_currency")
        values = self._definition_dict(data)
        self._validate_params(values["default_params"])
        defn = CurveDefinition(**values)
        self.s.add(defn)
        self.s.flush()
        audit.record(
            self.s, actor=actor, entity_type="curve_definition", entity_id=data.code, action="create",
            summary=f"Создана кривая {data.code}", changes=diff(None, values),
        )
        self.s.commit()
        return defn

    def update_definition(self, code: str, data: DefinitionBase, actor: Principal) -> CurveDefinition:
        defn = self.get_definition(code, lock=True)
        if not self.s.get(Currency, data.currency_code):
            raise AppError(f"Валюта {data.currency_code} не найдена", code="unknown_currency")
        values = self._definition_dict(data)
        self._validate_params(values["default_params"])
        before = {k: getattr(defn, k) for k in values}
        changes = diff(before, values)
        for k, v in values.items():
            setattr(defn, k, v)
        if changes:
            audit.record(
                self.s, actor=actor, entity_type="curve_definition", entity_id=code, action="update",
                summary=f"Изменены параметры кривой {code}", changes=changes,
            )
        self.s.commit()
        return defn

    # ------------------------------------------------------------------ calculation
    def calibrate(
        self, defn: CurveDefinition, as_of: date, payload: QuotesPayload
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        """Returns the result, cleaned quotes and the requested parameters.

        The result carries the resolved parameters (e.g. the actual start of the flat long
        end); the requested ones keep "auto" values and are what we store for re-editing.
        """
        try:
            quotes = parse_quotes(payload.quotes, payload.input_mode)
            params = effective_params(defn, payload.params.overrides())
            conv = conventions_of(defn)
            res = calibrate(as_of, quotes, conv, params)
        except (QuoteError, ValueError) as exc:
            raise AppError(str(exc), code="invalid_input") from None
        return result_to_dict(res, conv), _clean_quotes(payload.quotes, payload.input_mode), params.to_dict()

    @staticmethod
    def evaluate(req: EvaluateRequest) -> dict[str, Any]:
        try:
            nodes = sorted(req.nodes, key=lambda n: str(n["date"]))
            curve = DiscountCurve.from_nodes(
                req.as_of_date,
                DayCount(req.day_count),
                [date.fromisoformat(str(n["date"])) for n in nodes],
                [float(n["df"]) for n in nodes],
            )
        except (KeyError, ValueError) as exc:
            raise AppError(f"Некорректные узлы кривой: {exc}", code="invalid_nodes") from None
        if req.date <= req.as_of_date:
            raise AppError("Дата должна быть позже даты кривой", code="invalid_date")
        out: dict[str, Any] = {
            "date": req.date,
            "t": curve.t(req.date),
            "df": curve.df(req.date),
            "zero_rates": {c.value: curve.zero_rate(req.date, c) * 100 for c in Compounding},
            "instantaneous_forward": curve.instantaneous_forward(req.date) * 100,
            "extrapolated": curve.t(req.date) > float(curve.times[-1]),
            "forward_rates": None,
        }
        if req.start_date:
            if req.start_date < req.as_of_date or req.start_date >= req.date:
                raise AppError("Дата начала форварда должна быть между датой кривой и датой окончания", code="invalid_date")
            out["forward_rates"] = {c.value: curve.forward_rate(req.start_date, req.date, c) * 100 for c in Compounding}
        return out

    # ------------------------------------------------------------------ current state
    def get_state(self, code: str) -> tuple[CurveDefinition, CurveState]:
        defn = self.get_definition(code)
        state = self.s.get(CurveState, defn.id)
        if not state:
            raise NotFound(f"Для кривой {code} ещё нет текущего состояния")
        return defn, state

    def _history_exists(self, curve_id: Any, as_of: date) -> bool:
        return bool(
            self.s.scalar(
                select(func.count()).select_from(CurveHistory).where(
                    CurveHistory.curve_id == curve_id, CurveHistory.as_of_date == as_of
                )
            )
        )

    def _archive(self, defn: CurveDefinition, state: CurveState, source: str, actor: Principal) -> CurveHistory:
        row = CurveHistory(
            curve_id=defn.id,
            as_of_date=state.as_of_date,
            version=1,
            source=source,
            input_mode=state.input_mode,
            quotes=state.quotes,
            params=state.params,
            conventions=state.conventions,
            result=state.result,
            comment=state.comment,
            created_by=state.saved_by,
        )
        self.s.add(row)
        audit.record(
            self.s, actor=actor, entity_type="curve", entity_id=defn.code, action="history_archive",
            summary=f"Кривая {defn.code} на {state.as_of_date} перенесена в историю ({source})",
            changes={"as_of_date": state.as_of_date, "version": 1, "source": source},
        )
        return row

    def save_state(self, code: str, as_of: date, payload: QuotesPayload, comment: str, actor: Principal) -> CurveState:
        defn = self.get_definition(code, lock=True)  # serialises writes per curve
        today = self.settings.today()
        if as_of > today:
            raise AppError("Нельзя сохранить кривую на будущую дату", code="future_date")
        if self._history_exists(defn.id, as_of):
            raise Conflict(
                f"Кривая на {as_of} уже в истории — используйте редактирование исторической кривой",
                code="date_in_history",
            )
        state = self.s.get(CurveState, defn.id)
        if state and state.as_of_date > as_of:
            raise Conflict(f"Текущая кривая уже построена на более позднюю дату {state.as_of_date}", code="state_newer")
        result, quotes, params = self.calibrate(defn, as_of, payload)
        if state and state.as_of_date < as_of and not self._history_exists(defn.id, state.as_of_date):
            self._archive(defn, state, "auto_archive", actor)

        new_values = {
            "as_of_date": as_of,
            "input_mode": payload.input_mode.value,
            "quotes": quotes,
            "params": params,
            "conventions": result["conventions"],
            "result": result,
            "comment": comment,
        }
        before = None
        if state is None:
            state = CurveState(curve_id=defn.id, saved_by=actor.username, **new_values)
            self.s.add(state)
        else:
            before = {k: getattr(state, k) for k in ("as_of_date", "input_mode", "quotes", "params", "comment")}
            for k, v in new_values.items():
                setattr(state, k, v)
            state.saved_by = actor.username
            state.saved_at = func.now()
        after = {k: new_values[k] for k in ("as_of_date", "input_mode", "quotes", "params", "comment")}
        audit.record(
            self.s, actor=actor, entity_type="curve", entity_id=code, action="state_save",
            summary=f"Сохранена текущая кривая {code} на {as_of}", changes=diff(before, after),
        )
        self.s.commit()
        self.s.refresh(state)
        return state

    # ------------------------------------------------------------------ history
    def list_history(self, code: str, date_from: date | None, date_to: date | None) -> list[dict[str, Any]]:
        defn = self.get_definition(code)
        stmt = (
            select(CurveHistory)
            .options(load_only(CurveHistory.as_of_date, CurveHistory.version, CurveHistory.source,
                               CurveHistory.created_by, CurveHistory.created_at))
            .where(CurveHistory.curve_id == defn.id)
            .order_by(CurveHistory.as_of_date.desc(), CurveHistory.version.desc())
        )
        if date_from:
            stmt = stmt.where(CurveHistory.as_of_date >= date_from)
        if date_to:
            stmt = stmt.where(CurveHistory.as_of_date <= date_to)
        out: dict[date, dict[str, Any]] = {}
        for row in self.s.scalars(stmt):
            item = out.get(row.as_of_date)
            if item is None:
                out[row.as_of_date] = {
                    "as_of_date": row.as_of_date,
                    "latest_version": row.version,
                    "versions": 1,
                    "source": row.source,
                    "author": row.created_by,
                    "saved_at": row.created_at,
                    "edited": row.version > 1 or row.source == "manual_edit",
                }
            else:
                item["versions"] += 1
        return list(out.values())

    def list_versions(self, code: str, as_of: date) -> list[CurveHistory]:
        defn = self.get_definition(code)
        return list(
            self.s.scalars(
                select(CurveHistory)
                .options(load_only(CurveHistory.version, CurveHistory.source, CurveHistory.created_by,
                                   CurveHistory.created_at, CurveHistory.comment))
                .where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date == as_of)
                .order_by(CurveHistory.version.desc())
            )
        )

    def get_history(self, code: str, as_of: date, version: int | None) -> tuple[CurveDefinition, CurveHistory]:
        defn = self.get_definition(code)
        stmt = select(CurveHistory).where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date == as_of)
        stmt = stmt.where(CurveHistory.version == version) if version else stmt.order_by(CurveHistory.version.desc())
        row = self.s.scalars(stmt.limit(1)).first()
        if not row:
            raise NotFound(f"Нет исторической кривой {code} на {as_of}" + (f" версии {version}" if version else ""))
        return defn, row

    def save_history(self, code: str, as_of: date, payload: QuotesPayload, comment: str, actor: Principal) -> CurveHistory:
        defn = self.get_definition(code, lock=True)
        if as_of > self.settings.today():
            raise AppError("Нельзя создать историческую кривую на будущую дату", code="future_date")
        latest = self.s.scalars(
            select(CurveHistory)
            .where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date == as_of)
            .order_by(CurveHistory.version.desc())
            .limit(1)
        ).first()
        state = self.s.get(CurveState, defn.id)
        if latest is None and state is not None and state.as_of_date == as_of:
            raise Conflict(
                f"Кривая на {as_of} — текущая и ещё не в истории; отредактируйте текущую кривую",
                code="date_is_current",
            )
        result, quotes, params = self.calibrate(defn, as_of, payload)
        version = (latest.version + 1) if latest else 1
        row = CurveHistory(
            curve_id=defn.id,
            as_of_date=as_of,
            version=version,
            source="manual_edit",
            input_mode=payload.input_mode.value,
            quotes=quotes,
            params=params,
            conventions=result["conventions"],
            result=result,
            comment=comment,
            created_by=actor.username,
        )
        self.s.add(row)
        keys = ("input_mode", "quotes", "params")
        before = {k: getattr(latest, k) for k in keys} if latest else None
        after = {"input_mode": row.input_mode, "quotes": quotes, "params": params}
        changes = diff(before, after)
        changes["version"] = {"old": latest.version if latest else None, "new": version}
        audit.record(
            self.s, actor=actor, entity_type="curve", entity_id=code, action="history_edit",
            summary=f"Изменение задним числом: {code} на {as_of}, версия {version}. {comment}",
            changes=changes, tags=[TAG_BACKDATED],
        )
        self.s.commit()
        return row

    # ------------------------------------------------------------------ comparison
    def compare(self, code: str, as_of: date) -> list[dict[str, Any]]:
        defn = self.get_definition(code)
        state = self.s.get(CurveState, defn.id)
        targets = [
            ("day", as_of - timedelta(days=1)),
            ("week", as_of - timedelta(days=7)),
            ("month", as_of - relativedelta(months=1)),
        ]
        out = []
        for label, target in targets:
            hist = self.s.scalars(
                select(CurveHistory)
                .where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date <= target)
                .order_by(CurveHistory.as_of_date.desc(), CurveHistory.version.desc())
                .limit(1)
            ).first()
            src: CurveHistory | CurveState | None = hist
            if state is not None and state.as_of_date <= target and (hist is None or state.as_of_date > hist.as_of_date):
                src = state
            if src is None:
                continue
            out.append(
                {
                    "label": label,
                    "target_date": target,
                    "as_of_date": src.as_of_date,
                    "kind": "state" if isinstance(src, CurveState) else "history",
                    "version": getattr(src, "version", None),
                    "nodes": src.result.get("nodes", []),
                }
            )
        return out

    # ------------------------------------------------------------------ nightly archive
    def archive_states(self, up_to: date, actor: Principal, curve_code: str | None = None) -> dict[str, Any]:
        stmt = select(CurveDefinition, CurveState).join(CurveState, CurveState.curve_id == CurveDefinition.id)
        if curve_code:
            stmt = stmt.where(CurveDefinition.code == curve_code)
        archived, skipped = [], []
        for defn, state in self.s.execute(stmt.with_for_update(of=CurveDefinition)).all():
            item = {"curve": defn.code, "as_of_date": state.as_of_date.isoformat()}
            if state.as_of_date > up_to or self._history_exists(defn.id, state.as_of_date):
                skipped.append(item)
                continue
            self._archive(defn, state, "snapshot", actor)
            archived.append(item)
        self.s.commit()
        return {"up_to": up_to.isoformat(), "archived": archived, "skipped": skipped}
