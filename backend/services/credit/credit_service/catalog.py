"""Use cases around reference data: tracked issuers, their bonds, operator data, settings."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cds_common.audit import AuditLog, diff
from cds_common.auth import Principal
from cds_common.errors import AppError, Conflict, NotFound
from cds_common.tasks import Trigger, enqueue

from . import settings_store
from .config import CreditSettings
from .domain.bonds import BondProfile, classify
from .iss import IssClient
from .models import Bond, BondDaily, BondSnapshot, CurveBond, CurveDefinition, Issuer
from .schemas import BondManual, SettingsIn

audit = AuditLog("credit")
SCAN_TASK = "credit.scan_issuers"


def _fmt_money(v: float) -> str:
    return f"{v / 1e6:.1f} млн ₽" if v >= 1e5 else f"{v:,.0f} ₽".replace(",", " ")


class CatalogApp:
    def __init__(self, session: Session, settings: CreditSettings, iss: IssClient | None = None):
        self.s = session
        self.settings = settings
        self.iss = iss

    # ------------------------------------------------------------------ issuers
    def search_iss(self, query: str) -> list[dict[str, Any]]:
        if len(query.strip()) < 2:
            raise AppError("Введите хотя бы 2 символа", code="query_too_short")
        assert self.iss is not None
        rows = self.iss.search_emitters(query.strip())
        tracked = set(self.s.scalars(select(Issuer.emitter_id)))
        return [
            {"emitter_id": r["EMITTER_ID"], "title": r.get("TITLE") or "", "short_title": r.get("SHORT_TITLE"),
             "inn": r.get("INN"), "tracked": r["EMITTER_ID"] in tracked}
            for r in rows
        ]

    def list_issuers(self) -> list[dict[str, Any]]:
        today = self.settings.today()
        cfg = settings_store.load(self.s)
        bonds = self.s.scalars(select(Bond).where(Bond.is_traded.is_(True))).all()
        curves: dict[int, list[str]] = {}
        for issuer_id, code in self.s.execute(select(CurveDefinition.issuer_id, CurveDefinition.code)):
            curves.setdefault(issuer_id, []).append(code)
        inns = dict(self.s.execute(select(Issuer.emitter_id, Issuer.inn)).all())
        counts: dict[int, dict[str, int]] = {}
        for b in bonds:
            c = counts.setdefault(b.issuer_id, {"bonds_total": 0, "bonds_eligible": 0, "bonds_recommended": 0, "bonds_incomplete": 0})
            p = self.profile(b, today, inns.get(b.issuer_id))
            c["bonds_total"] += 1
            if p.eligible:
                c["bonds_eligible"] += 1
                if not p.complete:
                    c["bonds_incomplete"] += 1
                if self.recommendation(b, p, today, cfg, 0.5)[0]:
                    c["bonds_recommended"] += 1
        out = []
        for issuer in self.s.scalars(select(Issuer).order_by(Issuer.short_title, Issuer.title)):
            item = {k: getattr(issuer, k) for k in ("emitter_id", "title", "short_title", "inn", "url", "added_by", "added_at",
                                                     "scan_status", "scan_error", "scanned_at")}
            item.update(counts.get(issuer.emitter_id, {}))
            item["curves"] = sorted(curves.get(issuer.emitter_id, []))
            out.append(item)
        return out

    def add_issuer(self, emitter_id: int, actor: Principal) -> tuple[Issuer, str]:
        if self.s.get(Issuer, emitter_id):
            raise Conflict("Эмитент уже отслеживается")
        assert self.iss is not None
        info = self.iss.emitter(emitter_id)
        if not info:
            raise NotFound(f"Эмитент {emitter_id} не найден в ISS")
        issuer = Issuer(
            emitter_id=emitter_id, title=info.get("TITLE") or str(emitter_id), short_title=info.get("SHORT_TITLE") or "",
            inn=info.get("INN"), ogrn=info.get("OGRN"), url=info.get("URL"), added_by=actor.username, scan_status="queued",
        )
        self.s.add(issuer)
        self.s.flush()
        audit.record(self.s, actor=actor, entity_type="issuer", entity_id=str(emitter_id), action="create",
                     summary=f"Добавлен эмитент {issuer.short_title or issuer.title}",
                     changes=diff(None, {"emitter_id": emitter_id, "title": issuer.title, "inn": issuer.inn}))
        # the first scan of its bonds starts right away
        task_id = enqueue(self.s, SCAN_TASK, {"issuer_id": emitter_id}, trigger=Trigger.USER, created_by=actor.username)
        self.s.commit()
        return issuer, str(task_id)

    def rescan(self, emitter_id: int | None, actor: Principal) -> str:
        if emitter_id is not None:
            issuer = self.s.get(Issuer, emitter_id)
            if not issuer:
                raise NotFound("Эмитент не отслеживается")
            issuer.scan_status = "queued"
        task_id = enqueue(self.s, SCAN_TASK, {"issuer_id": emitter_id} if emitter_id else {}, trigger=Trigger.USER,
                          created_by=actor.username)
        self.s.commit()
        return str(task_id)

    def delete_issuer(self, emitter_id: int, actor: Principal) -> None:
        issuer = self.s.get(Issuer, emitter_id)
        if not issuer:
            raise NotFound("Эмитент не отслеживается")
        if self.s.scalar(select(func.count()).select_from(CurveDefinition).where(CurveDefinition.issuer_id == emitter_id)):
            raise Conflict("По эмитенту есть кредитные кривые — сначала удалите их или смените эмитента")
        for bond in self.s.scalars(select(Bond).where(Bond.issuer_id == emitter_id)):
            self.s.delete(bond)
        self.s.flush()
        self.s.delete(issuer)
        audit.record(self.s, actor=actor, entity_type="issuer", entity_id=str(emitter_id), action="delete",
                     summary=f"Эмитент {issuer.short_title or issuer.title} больше не отслеживается")
        self.s.commit()

    # ------------------------------------------------------------------ bonds
    @staticmethod
    def profile(bond: Bond, today: date, issuer_inn: str | None) -> BondProfile:
        return classify(bond.secid, bond.desc or {}, bond.schedule or {}, bond.manual or {}, today=today,
                        issuer_inn=issuer_inn, is_traded=bond.is_traded)

    @staticmethod
    def recommendation(bond: Bond, p: BondProfile, today: date, cfg: dict[str, Any], short_years: float) -> tuple[bool, list[str], bool]:
        notes: list[str] = list(p.ineligible)
        eff = p.effective_maturity
        years = (eff - today).days / 365.0 if eff else None
        short = years is not None and years < short_years
        if p.eligible:
            notes += [f"Нет данных: {m}" for m in p.missing]
            liq = bond.liquidity or {}
            turnover, trades = liq.get("avg_turnover"), liq.get("avg_trades")
            min_turnover = float(cfg["liquidity"]["min_avg_turnover"])
            min_trades = float(cfg["liquidity"]["min_avg_trades"])
            if turnover is None:
                notes.append("Нет статистики торгов")
            elif turnover < min_turnover:
                notes.append(f"Низкий оборот: {_fmt_money(turnover)} в день")
            if trades is not None and trades < min_trades:
                notes.append(f"Мало сделок: {trades:.1f} в день")
            if short:
                notes.append(f"До {'оферты' if p.offer_date and eff == p.offer_date else 'погашения'} меньше {short_years:g} г. — рекомендуется исключить")
        recommended = p.eligible and not notes
        return recommended, notes, short

    def bond_out(self, bond: Bond, today: date, inn: str | None, cfg: dict[str, Any], short_years: float, curves: list[str]) -> dict[str, Any]:
        p = self.profile(bond, today, inn)
        recommended, notes, short = self.recommendation(bond, p, today, cfg, short_years)
        eff = p.effective_maturity
        desc = bond.desc or {}
        try:
            list_level = int(desc.get("LISTLEVEL")) if desc.get("LISTLEVEL") else None
        except ValueError:
            list_level = None
        coupon = desc.get("COUPONPERCENT")
        return {
            "secid": bond.secid, "isin": bond.isin, "shortname": bond.shortname, "name": bond.name, "issuer_id": bond.issuer_id,
            "board": bond.board, "is_traded": bond.is_traded, "face_unit": bond.face_unit, "face_value": bond.face_value,
            "initial_face": p.initial_face, "coupon_pct": float(coupon) if coupon not in (None, "") else None,
            "list_level": list_level, "profile": p.to_dict(),
            "years_to_maturity": round((eff - today).days / 365.0, 3) if eff else None,
            "liquidity": bond.liquidity or {}, "liquidity_at": bond.liquidity_at,
            "recommended": recommended, "notes": notes, "short": short,
            "manual": bond.manual or {}, "manual_updated_by": bond.manual_updated_by, "manual_updated_at": bond.manual_updated_at,
            "curves": curves, "updated_at": bond.updated_at,
        }

    def list_bonds(self, issuer_id: int | None, short_years: float = 0.5, include_untraded: bool = False) -> list[dict[str, Any]]:
        today = self.settings.today()
        cfg = settings_store.load(self.s)
        stmt = select(Bond)
        if issuer_id is not None:
            stmt = stmt.where(Bond.issuer_id == issuer_id)
        if not include_untraded:
            stmt = stmt.where(Bond.is_traded.is_(True))
        inns = dict(self.s.execute(select(Issuer.emitter_id, Issuer.inn)).all())
        in_curves: dict[str, list[str]] = {}
        for secid, code in self.s.execute(select(CurveBond.secid, CurveDefinition.code).join(CurveDefinition, CurveDefinition.id == CurveBond.curve_id)):
            in_curves.setdefault(secid, []).append(code)
        bonds = self.s.scalars(stmt.order_by(Bond.mat_date.nulls_last(), Bond.secid)).all()
        out = [self.bond_out(b, today, inns.get(b.issuer_id), cfg, short_years, sorted(in_curves.get(b.secid, []))) for b in bonds]
        out.sort(key=lambda b: (b["years_to_maturity"] is None, b["years_to_maturity"] or 0.0, b["secid"]))
        return out

    def get_bond(self, secid: str) -> Bond:
        bond = self.s.get(Bond, secid)
        if not bond:
            raise NotFound(f"Облигация {secid} не найдена")
        return bond

    def bond_detail(self, secid: str) -> dict[str, Any]:
        bond = self.get_bond(secid)
        today = self.settings.today()
        cfg = settings_store.load(self.s)
        issuer = self.s.get(Issuer, bond.issuer_id)
        curves = sorted(self.s.scalars(select(CurveDefinition.code).join(CurveBond, CurveBond.curve_id == CurveDefinition.id).where(CurveBond.secid == secid)))
        out = self.bond_out(bond, today, issuer.inn if issuer else None, cfg, 0.5, curves)
        daily = self.s.scalars(
            select(BondDaily).where(BondDaily.secid == secid, BondDaily.trade_date >= today - timedelta(days=120))
            .order_by(BondDaily.trade_date.desc())
        ).all()
        cols = ("trade_date", "face_value", "accrued", "num_trades", "turnover", "close", "waprice", "bid", "ask", "last", "vwap",
                "dema", "dema_half_life", "spread_pct", "eod_face_value", "eod_accrued")
        snaps = self.s.scalars(
            select(BondSnapshot).where(BondSnapshot.secid == secid).order_by(BondSnapshot.fetched_at)
        ).all()
        scols = ("fetched_at", "market_time", "trade_date", "bid", "ask", "last", "vwap", "num_trades", "turnover", "face_value",
                 "accrued", "settle_date", "dema", "dema_half_life")
        out.update(
            desc=bond.desc or {}, schedule=bond.schedule or {},
            daily=[{c: getattr(r, c) for c in cols} for r in daily],
            intraday=[{c: getattr(r, c) for c in scols} for r in snaps],
        )
        return out

    def update_manual(self, secid: str, data: BondManual, actor: Principal) -> Bond:
        bond = self.get_bond(secid)
        new = {k: v for k, v in data.model_dump(mode="json").items() if v not in (None, "", False)}
        before = dict(bond.manual or {})
        changes = diff(before, new)
        if changes:
            bond.manual = new
            bond.manual_updated_by = actor.username
            bond.manual_updated_at = datetime.now(timezone.utc)
            audit.record(self.s, actor=actor, entity_type="bond", entity_id=secid, action="manual_update",
                         summary=f"Изменены данные облигации {bond.shortname or secid}", changes=changes)
        self.s.commit()
        return bond

    # ------------------------------------------------------------------ settings
    def get_settings(self) -> dict[str, Any]:
        return settings_store.load(self.s)

    def update_settings(self, data: SettingsIn, actor: Principal) -> dict[str, Any]:
        liq = data.liquidity
        if not 10 <= liq.get("window_days", 90) <= 730:
            raise AppError("Окно статистики ликвидности — от 10 до 730 дней", code="invalid_settings")
        dema_cfg = data.dema
        if dema_cfg.get("half_life_hours") is not None and not 0.5 <= float(dema_cfg["half_life_hours"]) <= 1000:
            raise AppError("Период полураспада DEMA — от 0.5 до 1000 торговых часов", code="invalid_settings")
        if not 10 <= float(dema_cfg.get("lookback_days") or 90) <= 365:
            raise AppError("Глубина свечей для DEMA — от 10 до 365 дней", code="invalid_settings")
        settings_store.save(self.s, "index_curves", {k.value: v for k, v in data.index_curves.items()}, actor)
        settings_store.save(self.s, "liquidity", {k: float(v) for k, v in liq.items()}, actor)
        settings_store.save(self.s, "dema", {"half_life_hours": dema_cfg.get("half_life_hours"),
                                            "lookback_days": int(dema_cfg.get("lookback_days") or 90)}, actor)
        self.s.commit()
        return settings_store.load(self.s)
