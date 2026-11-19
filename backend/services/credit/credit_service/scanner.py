"""Daily scan of tracked issuers on the exchange (and the first scan right after an issuer is added).

For every issuer: list of its bonds -> static data and schedule of each bond -> daily trading
history (turnover, trades, accrued interest, notional) -> liquidity metrics.
Static data are refreshed every day: coupons of floaters get fixed, offers get announced.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from .config import CreditSettings
from .domain.bonds import classify
from .domain.liquidity import liquidity_metrics
from .iss import IssClient, IssError
from .models import Bond, BondDaily, Issuer
from . import settings_store

log = logging.getLogger(__name__)

# Bonds with these ISS properties are kept in the list (with the reason) but not scanned further.
_SKIP_REASONS = ("Валюта номинала", "Структурная", "Субординированная", "Дефолт", "Не торгуется", "Погашена")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _date(v: Any) -> date | None:
    if not v or str(v).startswith("0000"):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def history_rows(raw: list[dict[str, Any]], board: str | None) -> dict[date, dict[str, Any]]:
    """ISS history -> {trade_date: values}; the primary board wins when a bond trades on several."""
    out: dict[date, dict[str, Any]] = {}
    for r in raw:
        d = _date(r.get("TRADEDATE"))
        if d is None:
            continue
        if d in out and board and r.get("BOARDID") != board:
            continue
        out[d] = {
            "face_value": _num(r.get("FACEVALUE")),
            "accrued": _num(r.get("ACCINT")),
            "num_trades": int(r.get("NUMTRADES") or 0),
            "turnover": _num(r.get("VALUE")) or 0.0,
            "close": _num(r.get("CLOSE")),
            "waprice": _num(r.get("WAPRICE")),
        }
    return out


class Scanner:
    def __init__(self, session_factory: sessionmaker, settings: CreditSettings, iss: IssClient):
        self.sf = session_factory
        self.settings = settings
        self.iss = iss

    def scan(self, issuer_id: int | None = None) -> dict[str, Any]:
        with self.sf() as s:
            stmt = select(Issuer.emitter_id)
            if issuer_id is not None:
                stmt = stmt.where(Issuer.emitter_id == issuer_id)
            ids = list(s.scalars(stmt.order_by(Issuer.emitter_id)))
        report: dict[str, Any] = {"issuers": [], "errors": []}
        for eid in ids:
            try:
                report["issuers"].append(self.scan_issuer(eid))
            except Exception as exc:  # noqa: BLE001 - one issuer must not stop the others
                log.exception("Scan of issuer %s failed", eid)
                report["errors"].append({"issuer_id": eid, "error": str(exc)})
                with self.sf() as s, s.begin():
                    issuer = s.get(Issuer, eid)
                    if issuer:
                        issuer.scan_status, issuer.scan_error = "error", str(exc)[:2000]
        if report["errors"] and not report["issuers"]:
            raise IssError("; ".join(e["error"] for e in report["errors"])[:1000])
        return report

    def scan_issuer(self, emitter_id: int) -> dict[str, Any]:
        today = self.settings.today()
        with self.sf() as s, s.begin():
            issuer = s.get(Issuer, emitter_id)
            if issuer is None:
                raise ValueError(f"Эмитент {emitter_id} не отслеживается")
            issuer.scan_status, issuer.scan_error = "running", None
            inn = issuer.inn
            cfg = settings_store.load(s)
        window = int(cfg["liquidity"]["window_days"])

        info = self.iss.emitter(emitter_id)
        listed = [b for b in self.iss.emitter_bonds(emitter_id) if b.get("IS_TRADED")]
        boards = {b.get("PRIMARY_BOARDID") for b in listed if b.get("PRIMARY_BOARDID")}
        market: dict[str, dict[str, Any]] = {}
        for board in sorted(boards):
            try:
                market.update(self.iss.board_market(board))
            except IssError as exc:
                log.warning("Board %s market data unavailable: %s", board, exc)

        listed_ids = {b["SECID"] for b in listed}
        stats = {"issuer_id": emitter_id, "bonds": len(listed), "new": 0, "scanned": 0, "skipped": 0}
        for item in listed:
            secid = item["SECID"]
            desc = self.iss.description(secid)
            m = market.get(secid, {})
            for key in ("OFFERDATE", "PUTOPTIONDATE", "CALLOPTIONDATE"):
                if m.get(key):
                    desc[key] = m[key]
            with self.sf() as s, s.begin():
                bond = s.get(Bond, secid)
                if bond is None:
                    bond = Bond(secid=secid, issuer_id=emitter_id)
                    s.add(bond)
                    stats["new"] += 1
                bond.issuer_id = emitter_id
                bond.isin = item.get("ISIN")
                bond.shortname = item.get("SHORTNAME") or ""
                bond.name = item.get("NAME") or ""
                bond.board = item.get("PRIMARY_BOARDID")
                bond.is_traded = True
                bond.face_unit = desc.get("FACEUNIT") or item.get("FACEUNIT")
                bond.face_value = _num(m.get("FACEVALUE")) or _num(desc.get("FACEVALUE")) or _num(item.get("FACEVALUE"))
                bond.mat_date = _date(desc.get("MATDATE"))
                bond.desc = desc
                bond.updated_at = datetime.now(timezone.utc)
                manual = dict(bond.manual or {})
                history_until = bond.history_until
            static = classify(secid, desc, None, manual, today=today, issuer_inn=inn)
            if any(r.startswith(_SKIP_REASONS) for r in static.ineligible):
                stats["skipped"] += 1
                continue
            schedule = self.iss.bondization(secid)
            start = max(history_until + timedelta(days=1), today - timedelta(days=window + 7)) if history_until else today - timedelta(days=window + 7)
            hist = history_rows(self.iss.history(secid, start), item.get("PRIMARY_BOARDID")) if start <= today else {}
            with self.sf() as s, s.begin():
                bond = s.get(Bond, secid)
                bond.schedule = schedule
                for d, values in hist.items():
                    s.execute(
                        pg_insert(BondDaily).values(secid=secid, trade_date=d, **values)
                        .on_conflict_do_update(index_elements=["secid", "trade_date"], set_=values)
                    )
                if hist:
                    bond.history_until = max(hist)
                s.flush()
                bond.liquidity = self._liquidity(s, secid, window, today)
                bond.liquidity_at = datetime.now(timezone.utc)
            stats["scanned"] += 1

        with self.sf() as s, s.begin():
            issuer = s.get(Issuer, emitter_id)
            if info:
                issuer.title = info.get("TITLE") or issuer.title
                issuer.short_title = info.get("SHORT_TITLE") or issuer.short_title
                issuer.inn = info.get("INN") or issuer.inn
                issuer.ogrn = info.get("OGRN") or issuer.ogrn
                issuer.url = info.get("URL") or issuer.url
            for bond in s.scalars(select(Bond).where(Bond.issuer_id == emitter_id, Bond.is_traded.is_(True))):
                if bond.secid not in listed_ids:
                    bond.is_traded = False  # matured or delisted
            issuer.scan_status, issuer.scan_error = "ok", None
            issuer.scanned_at = datetime.now(timezone.utc)
        log.info("Issuer %s scanned: %s", emitter_id, stats)
        return stats

    @staticmethod
    def _liquidity(s: Session, secid: str, window: int, today: date) -> dict[str, Any]:
        since = today - timedelta(days=window)
        rows = s.execute(
            select(BondDaily.trade_date, BondDaily.turnover, BondDaily.num_trades, BondDaily.spread_pct)
            .where(BondDaily.secid == secid, BondDaily.trade_date >= since)
        ).all()
        days = [(r.trade_date, r.turnover, r.num_trades) for r in rows if r.turnover is not None or r.num_trades is not None]
        spreads = [r.spread_pct for r in rows if r.spread_pct is not None]
        return liquidity_metrics(days, spreads, window_days=window, as_of=today)


def recompute_liquidity(session: Session, secids: list[str], window: int, today: date) -> None:
    for secid in secids:
        bond = session.get(Bond, secid)
        if bond is not None:
            bond.liquidity = Scanner._liquidity(session, secid, window, today)
            bond.liquidity_at = datetime.now(timezone.utc)
