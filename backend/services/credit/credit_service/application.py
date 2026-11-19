"""Use cases of credit curves.

Lifecycle (same as interest-rate curves):
  * the curve is NOT recalculated automatically: the operator opens the online editor, the curve
    is bootstrapped from the latest market data, the operator adjusts parameters / composition and
    saves it as the CURRENT STATE (one row per curve, overwritten);
  * every night current states of past dates move to HISTORY (version 1);
  * a change of history adds version N+1, journaled with the tag "backdated"; it uses the
    end-of-day (19:00) market data of that date.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from cds_common.audit import TAG_BACKDATED, AuditLog, diff
from cds_common.auth import Principal
from cds_common.errors import AppError, Conflict, NotFound

from . import settings_store
from .catalog import CatalogApp
from .config import CreditSettings
from .domain.bonds import KIND_TITLES, BondKind, RateIndex
from .domain.calibration import BondFitInput, CreditParams, calibrate
from .domain.cashflows import CashflowError, accrued_interest, project
from .domain.pricing import BondPricer, HazardGrid
from .market import MarketData
from .models import Bond, CurveBond, CurveDefinition, CurveHistory, CurveState, Issuer
from .rates_client import RatesClient
from .schemas import CalcBondIn, CalcPayload, DefinitionBase, DefinitionIn

audit = AuditLog("credit")


def effective_params(defn: CurveDefinition, overrides: dict[str, Any] | None = None) -> CreditParams:
    return CreditParams().with_overrides(defn.default_params).with_overrides(overrides)


class CreditApp:
    def __init__(self, session: Session, settings: CreditSettings, market: MarketData | None = None):
        self.s = session
        self.settings = settings
        self.market = market

    # ------------------------------------------------------------------ definitions
    def list_definitions(self) -> list[CurveDefinition]:
        return list(self.s.scalars(select(CurveDefinition).order_by(CurveDefinition.code)))

    def get_definition(self, code: str, *, lock: bool = False) -> CurveDefinition:
        stmt = select(CurveDefinition).where(CurveDefinition.code == code)
        if lock:
            stmt = stmt.with_for_update()
        defn = self.s.scalars(stmt).first()
        if not defn:
            raise NotFound(f"Кредитная кривая {code} не найдена")
        return defn

    def composition(self, defn: CurveDefinition) -> list[CurveBond]:
        return list(self.s.scalars(select(CurveBond).where(CurveBond.curve_id == defn.id).order_by(CurveBond.added_at, CurveBond.secid)))

    def compositions(self) -> dict[Any, list[dict[str, Any]]]:
        rows = self.s.execute(
            select(CurveBond.curve_id, CurveBond.secid, CurveBond.weight, Bond.shortname, Bond.mat_date)
            .join(Bond, Bond.secid == CurveBond.secid).order_by(Bond.mat_date.nulls_last())
        ).all()
        out: dict[Any, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r.curve_id, []).append({"secid": r.secid, "weight": r.weight, "shortname": r.shortname, "mat_date": r.mat_date})
        return out

    def state_infos(self) -> dict[Any, CurveState]:
        rows = self.s.scalars(select(CurveState).options(load_only(CurveState.curve_id, CurveState.as_of_date, CurveState.saved_by, CurveState.saved_at)))
        return {r.curve_id: r for r in rows}

    def last_history_dates(self) -> dict[Any, date]:
        return dict(self.s.execute(select(CurveHistory.curve_id, func.max(CurveHistory.as_of_date)).group_by(CurveHistory.curve_id)).all())

    def issuer_titles(self) -> dict[int, str]:
        return {i.emitter_id: i.short_title or i.title for i in self.s.scalars(select(Issuer))}

    def _validate(self, data: DefinitionBase) -> dict[str, Any]:
        if not self.s.get(Issuer, data.issuer_id):
            raise AppError("Эмитент не отслеживается: добавьте его на странице «Эмитенты»", code="unknown_issuer")
        try:
            CreditParams().with_overrides(data.default_params.overrides()).validate()
        except ValueError as exc:
            raise AppError(str(exc), code="invalid_params") from None
        secids = [b.secid for b in data.bonds]
        found = dict(self.s.execute(select(Bond.secid, Bond.issuer_id).where(Bond.secid.in_(secids))).all()) if secids else {}
        unknown = [x for x in secids if x not in found]
        if unknown:
            raise AppError(f"Неизвестные бумаги: {', '.join(unknown)}", code="unknown_bonds")
        foreign = [x for x in secids if found[x] != data.issuer_id]
        if foreign:
            raise AppError(f"Бумаги другого эмитента: {', '.join(foreign)}", code="foreign_bonds")
        return {
            "name": data.name, "issuer_id": data.issuer_id, "discount_curve_code": data.discount_curve_code,
            "description": data.description, "default_params": data.default_params.overrides(), "is_active": data.is_active,
        }

    def _set_bonds(self, defn: CurveDefinition, bonds: list[Any], actor: Principal) -> dict[str, Any]:
        current = {b.secid: b for b in self.composition(defn)}
        wanted = {b.secid: b.weight for b in bonds}
        for secid, row in current.items():
            if secid not in wanted:
                self.s.delete(row)
            elif row.weight != wanted[secid]:
                row.weight = wanted[secid]
        for secid, weight in wanted.items():
            if secid not in current:
                self.s.add(CurveBond(curve_id=defn.id, secid=secid, weight=weight, added_by=actor.username))
        return diff({k: v.weight for k, v in current.items()}, wanted)

    def create_definition(self, data: DefinitionIn, actor: Principal) -> CurveDefinition:
        if self.s.scalars(select(CurveDefinition).where(CurveDefinition.code == data.code)).first():
            raise Conflict(f"Кривая с кодом {data.code} уже существует")
        values = self._validate(data)
        defn = CurveDefinition(code=data.code, **values)
        self.s.add(defn)
        self.s.flush()
        bond_changes = self._set_bonds(defn, data.bonds, actor)
        audit.record(self.s, actor=actor, entity_type="credit_curve_definition", entity_id=data.code, action="create",
                     summary=f"Создана кредитная кривая {data.code}", changes={**diff(None, values), "bonds": bond_changes})
        self.s.commit()
        return defn

    def update_definition(self, code: str, data: DefinitionBase, actor: Principal) -> CurveDefinition:
        defn = self.get_definition(code, lock=True)
        values = self._validate(data)
        changes = diff({k: getattr(defn, k) for k in values}, values)
        for k, v in values.items():
            setattr(defn, k, v)
        bond_changes = self._set_bonds(defn, data.bonds, actor)
        if bond_changes:
            changes["bonds"] = bond_changes
        if changes:
            audit.record(self.s, actor=actor, entity_type="credit_curve_definition", entity_id=code, action="update",
                         summary=f"Изменены параметры кредитной кривой {code}", changes=changes)
        self.s.commit()
        return defn

    # ------------------------------------------------------------------ market inputs
    def bond_inputs(self, defn: CurveDefinition, as_of: date, requested: list[CalcBondIn] | None) -> list[dict[str, Any]]:
        """Composition with the market observations of the date (what the editor shows before a calculation)."""
        assert self.market is not None
        comp = {b.secid: b for b in self.composition(defn)}
        if requested is None:
            requested = [CalcBondIn(secid=b.secid, weight=b.weight) for b in comp.values()]
        secids = [r.secid for r in requested]
        live = as_of == self.settings.today()
        obs = self.market.observations(secids, as_of, live=live)
        bonds = {b.secid: b for b in self.s.scalars(select(Bond).where(Bond.secid.in_(secids)))}
        issuer = self.s.get(Issuer, defn.issuer_id)
        params = effective_params(defn)
        out = []
        for r in requested:
            bond = bonds.get(r.secid)
            if bond is None:
                raise AppError(f"Облигация {r.secid} не найдена", code="unknown_bonds")
            p = CatalogApp.profile(bond, as_of, issuer.inn if issuer else None)
            eff = p.effective_maturity
            years = (eff - as_of).days / 365.0 if eff else None
            out.append({
                "secid": r.secid, "shortname": bond.shortname, "weight": r.weight, "enabled": r.enabled,
                "manual_price": r.manual_price, "excluded_obs": list(r.excluded_obs), "in_composition": r.secid in comp,
                "kind": p.kind.value if p.kind else None,
                "kind_title": KIND_TITLES.get(p.kind, "") if p.kind else "",
                "coupon_index": p.coupon_index.value if p.coupon_index else None,
                "spread_pct": p.spread_pct,
                "features": p.features, "eligible": p.eligible, "complete": p.complete,
                "problems": p.ineligible + p.missing,
                "maturity": eff.isoformat() if eff else None, "to_offer": bool(p.offer_date and eff == p.offer_date),
                "years": round(years, 3) if years is not None else None,
                "short": years is not None and years < params.short_bond_years,
                "market": obs[r.secid].to_dict(),
            })
        return out

    # ------------------------------------------------------------------ calculation
    def calculate(self, defn: CurveDefinition, as_of: date, payload: CalcPayload, rates: RatesClient) -> dict[str, Any]:
        """Bootstrap the curve (nothing is saved). Returns bonds (inputs), inputs (curves used) and result."""
        if as_of > self.settings.today():
            raise AppError("Нельзя строить кривую на будущую дату", code="future_date")
        params = effective_params(defn, payload.params.overrides())
        try:
            params.validate()
        except ValueError as exc:
            raise AppError(str(exc), code="invalid_params") from None
        bonds_in = self.bond_inputs(defn, as_of, payload.bonds)
        cfg = settings_store.load(self.s)
        issuer = self.s.get(Issuer, defn.issuer_id)
        db_bonds = {b.secid: b for b in self.s.scalars(select(Bond).where(Bond.secid.in_([b["secid"] for b in bonds_in])))}

        discount, discount_ref = rates.curve(defn.discount_curve_code, as_of)
        inputs: dict[str, Any] = {"discount": discount_ref, "projection": {}, "recovery_rate": params.recovery_rate}
        warnings: list[str] = []
        if discount_ref["as_of_date"] != as_of.isoformat():
            warnings.append(f"Кривая дисконтирования {defn.discount_curve_code} взята на {discount_ref['as_of_date']}")

        profiles = {}
        needed: set[RateIndex] = set()
        for b in bonds_in:
            if not b["enabled"]:
                continue
            p = CatalogApp.profile(db_bonds[b["secid"]], as_of, issuer.inn if issuer else None)
            profiles[b["secid"]] = p
            if p.kind is BondKind.FLOATING and p.coupon_index:
                needed.add(p.coupon_index)
            if p.kind is BondKind.LINKER:
                needed.add(p.notional_index or RateIndex.RUONIA)
        curves = {}
        for idx in sorted(needed, key=lambda i: i.value):
            code = cfg["index_curves"].get(idx.value)
            if not code:
                warnings.append(f"Не настроена кривая для индекса {idx.value}")
                continue
            try:
                curves[idx], ref = rates.curve(code, as_of)
            except AppError as exc:
                warnings.append(exc.detail)
                continue
            inputs["projection"][idx.value] = ref
            if ref["as_of_date"] != as_of.isoformat():
                warnings.append(f"Кривая {code} для индекса {idx.value} взята на {ref['as_of_date']}")

        grid = HazardGrid.monthly(as_of)
        fit_inputs: list[BondFitInput] = []
        for b in bonds_in:
            label = b["shortname"] or b["secid"]
            b["status"] = "excluded"
            if not b["enabled"]:
                continue
            p = profiles[b["secid"]]
            if not p.eligible or not p.complete:
                b["status"] = "skipped"
                warnings.append(f"{label}: пропущена — {'; '.join(p.ineligible + p.missing)}")
                continue
            m = b["market"]
            settle = date.fromisoformat(m["settle_date"])
            face = m["face"]
            try:
                accrued = m["accrued"]
                if accrued is None:
                    accrued = accrued_interest(p, curve_date=as_of, settle_date=settle, face_settle=face, curves=curves)
                    b["market"]["accrued_estimated"] = True
                projected = project(p, curve_date=as_of, settle_date=settle, face_settle=face, curves=curves)
            except (CashflowError, TypeError) as exc:
                b["status"] = "skipped"
                warnings.append(f"{label}: пропущена — {exc}")
                continue
            prices = {k: v for k, v in m["prices"].items() if k not in b["excluded_obs"]}
            if b["manual_price"]:
                prices["manual"] = b["manual_price"]
            if b["short"]:
                warnings.append(f"{label}: до {'оферты' if b['to_offer'] else 'погашения'} {b['years']:.2f} г. "
                                f"(меньше {params.short_bond_years:g}) — рекомендуется исключить")
            pricer = BondPricer(projected, grid, discount, params.recovery_rate, accrued)
            b["status"] = "used" if prices else "no_prices"
            fit_inputs.append(BondFitInput(b["secid"], label, pricer, prices, b["weight"],
                                           info={"kind": b["kind"], "short": b["short"], "to_offer": b["to_offer"]}))
            b["cashflows"] = [{"date": f.date.isoformat(), "kind": f.kind, "amount": round(f.amount, 6), "rate_pct": f.rate_pct,
                               "projected": f.projected} for f in projected.flows]

        result = None
        if fit_inputs and any(f.observations for f in fit_inputs):
            try:
                out = calibrate(as_of, fit_inputs, params)
            except ValueError as exc:
                raise AppError(str(exc), code="calibration_failed") from None
            result = {
                "as_of_date": as_of.isoformat(), "params": out.params, "nodes": out.nodes, "fitted": out.fitted,
                "stats": out.stats, "warnings": warnings + out.warnings,
            }
        else:
            warnings.append("Нет бумаг с котировками — кривую построить нельзя")
        return {"as_of_date": as_of, "bonds": bonds_in, "inputs": {**inputs, "warnings": warnings}, "result": result}

    @staticmethod
    def _stored_bonds(bonds_in: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keep = ("secid", "shortname", "weight", "enabled", "manual_price", "excluded_obs", "status", "kind", "maturity", "years", "market")
        return [{k: b.get(k) for k in keep} for b in bonds_in]

    @staticmethod
    def _requested_payload(payload: CalcPayload) -> dict[str, Any]:
        return payload.params.overrides()

    # ------------------------------------------------------------------ current state
    def get_state(self, code: str) -> tuple[CurveDefinition, CurveState]:
        defn = self.get_definition(code)
        state = self.s.get(CurveState, defn.id)
        if not state:
            raise NotFound(f"Для кривой {code} ещё нет текущего состояния")
        return defn, state

    def _history_exists(self, curve_id: Any, as_of: date) -> bool:
        return bool(self.s.scalar(select(func.count()).select_from(CurveHistory).where(CurveHistory.curve_id == curve_id, CurveHistory.as_of_date == as_of)))

    def _archive(self, defn: CurveDefinition, state: CurveState, source: str, actor: Principal) -> None:
        self.s.add(CurveHistory(
            curve_id=defn.id, as_of_date=state.as_of_date, version=1, source=source, bonds=state.bonds, params=state.params,
            inputs=state.inputs, result=state.result, comment=state.comment, created_by=state.saved_by,
        ))
        audit.record(self.s, actor=actor, entity_type="credit_curve", entity_id=defn.code, action="history_archive",
                     summary=f"Кредитная кривая {defn.code} на {state.as_of_date} перенесена в историю ({source})",
                     changes={"as_of_date": state.as_of_date, "version": 1, "source": source})

    def save_state(self, code: str, as_of: date, payload: CalcPayload, comment: str, actor: Principal, rates: RatesClient) -> CurveState:
        defn = self.get_definition(code, lock=True)
        if as_of > self.settings.today():
            raise AppError("Нельзя сохранить кривую на будущую дату", code="future_date")
        if self._history_exists(defn.id, as_of):
            raise Conflict(f"Кривая на {as_of} уже в истории — используйте редактирование исторической кривой", code="date_in_history")
        state = self.s.get(CurveState, defn.id)
        if state and state.as_of_date > as_of:
            raise Conflict(f"Текущая кривая уже построена на более позднюю дату {state.as_of_date}", code="state_newer")
        calc = self.calculate(defn, as_of, payload, rates)
        if calc["result"] is None:
            raise AppError("Кривая не построена: " + "; ".join(calc["inputs"]["warnings"][-3:]), code="calibration_failed")
        if state and state.as_of_date < as_of and not self._history_exists(defn.id, state.as_of_date):
            self._archive(defn, state, "auto_archive", actor)
        new_values = {
            "as_of_date": as_of, "bonds": self._stored_bonds(calc["bonds"]), "params": self._requested_payload(payload),
            "inputs": calc["inputs"], "result": calc["result"], "comment": comment,
        }
        audited = ("as_of_date", "params", "comment")
        before = None
        if state is None:
            state = CurveState(curve_id=defn.id, saved_by=actor.username, **new_values)
            self.s.add(state)
        else:
            before = {k: getattr(state, k) for k in audited}
            before["bonds"] = [{k: b.get(k) for k in ("secid", "weight", "enabled", "manual_price", "excluded_obs")} for b in state.bonds]
            for k, v in new_values.items():
                setattr(state, k, v)
            state.saved_by = actor.username
            state.saved_at = func.now()
        after = {k: new_values[k] for k in audited}
        after["bonds"] = [{k: b.get(k) for k in ("secid", "weight", "enabled", "manual_price", "excluded_obs")} for b in new_values["bonds"]]
        changes = diff(before, after)
        changes["stats"] = {"old": None, "new": {k: calc["result"]["stats"].get(k) for k in ("rmse_bp", "max_abs_bp", "n_bonds")}}
        audit.record(self.s, actor=actor, entity_type="credit_curve", entity_id=code, action="state_save",
                     summary=f"Сохранена текущая кредитная кривая {code} на {as_of}", changes=changes)
        self.s.commit()
        self.s.refresh(state)
        return state

    # ------------------------------------------------------------------ history
    def list_history(self, code: str, date_from: date | None, date_to: date | None) -> list[dict[str, Any]]:
        defn = self.get_definition(code)
        stmt = (
            select(CurveHistory)
            .options(load_only(CurveHistory.as_of_date, CurveHistory.version, CurveHistory.source, CurveHistory.created_by, CurveHistory.created_at))
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
                    "as_of_date": row.as_of_date, "latest_version": row.version, "versions": 1, "source": row.source,
                    "author": row.created_by, "saved_at": row.created_at, "edited": row.version > 1 or row.source == "manual_edit",
                }
            else:
                item["versions"] += 1
        return list(out.values())

    def list_versions(self, code: str, as_of: date) -> list[CurveHistory]:
        defn = self.get_definition(code)
        return list(self.s.scalars(
            select(CurveHistory)
            .options(load_only(CurveHistory.version, CurveHistory.source, CurveHistory.created_by, CurveHistory.created_at, CurveHistory.comment))
            .where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date == as_of)
            .order_by(CurveHistory.version.desc())
        ))

    def get_history(self, code: str, as_of: date, version: int | None) -> tuple[CurveDefinition, CurveHistory]:
        defn = self.get_definition(code)
        stmt = select(CurveHistory).where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date == as_of)
        stmt = stmt.where(CurveHistory.version == version) if version else stmt.order_by(CurveHistory.version.desc())
        row = self.s.scalars(stmt.limit(1)).first()
        if not row:
            raise NotFound(f"Нет исторической кривой {code} на {as_of}" + (f" версии {version}" if version else ""))
        return defn, row

    def save_history(self, code: str, as_of: date, payload: CalcPayload, comment: str, actor: Principal, rates: RatesClient) -> CurveHistory:
        defn = self.get_definition(code, lock=True)
        if as_of >= self.settings.today():
            raise AppError("Историческую кривую можно ввести только за прошедшую дату", code="future_date")
        latest = self.s.scalars(
            select(CurveHistory).where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date == as_of)
            .order_by(CurveHistory.version.desc()).limit(1)
        ).first()
        state = self.s.get(CurveState, defn.id)
        if latest is None and state is not None and state.as_of_date == as_of:
            raise Conflict(f"Кривая на {as_of} — текущая и ещё не в истории; отредактируйте текущую кривую", code="date_is_current")
        calc = self.calculate(defn, as_of, payload, rates)
        if calc["result"] is None:
            raise AppError("Кривая не построена: " + "; ".join(calc["inputs"]["warnings"][-3:]), code="calibration_failed")
        version = (latest.version + 1) if latest else 1
        row = CurveHistory(
            curve_id=defn.id, as_of_date=as_of, version=version, source="manual_edit", bonds=self._stored_bonds(calc["bonds"]),
            params=self._requested_payload(payload), inputs=calc["inputs"], result=calc["result"], comment=comment,
            created_by=actor.username,
        )
        self.s.add(row)
        pick = lambda bonds: [{k: b.get(k) for k in ("secid", "weight", "enabled", "manual_price", "excluded_obs")} for b in bonds]  # noqa: E731
        changes = diff(
            {"params": latest.params, "bonds": pick(latest.bonds)} if latest else None,
            {"params": row.params, "bonds": pick(row.bonds)},
        )
        changes["version"] = {"old": latest.version if latest else None, "new": version}
        audit.record(self.s, actor=actor, entity_type="credit_curve", entity_id=code, action="history_edit",
                     summary=f"Изменение задним числом: {code} на {as_of}, версия {version}. {comment}",
                     changes=changes, tags=[TAG_BACKDATED])
        self.s.commit()
        return row

    # ------------------------------------------------------------------ comparison and archive
    def compare(self, code: str, as_of: date) -> list[dict[str, Any]]:
        defn = self.get_definition(code)
        state = self.s.get(CurveState, defn.id)
        out = []
        for label, target in (("day", as_of - timedelta(days=1)), ("week", as_of - timedelta(days=7)), ("month", as_of - relativedelta(months=1))):
            hist = self.s.scalars(
                select(CurveHistory).where(CurveHistory.curve_id == defn.id, CurveHistory.as_of_date <= target)
                .order_by(CurveHistory.as_of_date.desc(), CurveHistory.version.desc()).limit(1)
            ).first()
            src: CurveHistory | CurveState | None = hist
            if state is not None and state.as_of_date <= target and (hist is None or state.as_of_date > hist.as_of_date):
                src = state
            if src is None:
                continue
            out.append({"label": label, "target_date": target, "as_of_date": src.as_of_date,
                        "kind": "state" if isinstance(src, CurveState) else "history",
                        "version": getattr(src, "version", None), "nodes": src.result.get("nodes", [])})
        return out

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
