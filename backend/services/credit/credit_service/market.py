"""Market data of bonds: 15-minute snapshots, hourly candles, DEMA, end-of-day values.

* every 15 minutes (task credit.refresh_prices) — order book / last trade / VWAP of every bond
  of tracked issuers (one ISS request per board) and hourly candles + DEMA of bonds in curves;
* after the main session (task credit.close_day) — values at 19:00 Moscow time are fixed in
  bond_daily (the history keeps only end-of-day values), the median intraday bid-ask spread is
  stored for liquidity statistics, snapshots of previous days are deleted.

Free ISS data are delayed (ISS_DELAY_MINUTES, 15 by default): a snapshot fetched at F describes
the market at F − delay.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from cds_common.auth import SYSTEM

from . import settings_store
from .config import CreditSettings
from .domain.dema import choose_half_life, dema, trading_clock
from .domain.liquidity import spread_pct
from .iss import IssClient, IssError
from .models import Bond, BondCandle, BondDaily, BondSnapshot, CurveBond, Issuer
from .scanner import recompute_liquidity

log = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")  # ISS times are always Moscow time
CLOSE_TIME = time(19, 0)  # end of the main session
SESSION_START = time(10, 0)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v and not str(v).startswith("0000") else None
    except ValueError:
        return None


def next_weekday(d: date) -> date:
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


@dataclass
class Observation:
    """Market inputs of one bond for a curve date."""

    secid: str
    prices: dict[str, float | None] = field(default_factory=dict)  # dema, bid, ask, last, vwap
    face: float | None = None
    accrued: float | None = None
    settle_date: date | None = None
    as_of: str | None = None  # time of the data (ISO)
    dema_half_life: float | None = None
    last_candle: str | None = None
    source: str = ""  # intraday | eod | none

    def to_dict(self) -> dict[str, Any]:
        return {
            "secid": self.secid,
            "prices": self.prices,
            "face": self.face,
            "accrued": self.accrued,
            "settle_date": self.settle_date.isoformat() if self.settle_date else None,
            "as_of": self.as_of,
            "dema_half_life": self.dema_half_life,
            "last_candle": self.last_candle,
            "source": self.source,
        }


class MarketData:
    def __init__(self, session_factory: sessionmaker, settings: CreditSettings, iss: IssClient | None, delay_minutes: int = 15):
        self.sf = session_factory
        self.settings = settings
        self.iss = iss
        self.delay = timedelta(minutes=delay_minutes)

    # ------------------------------------------------------------------ 15-minute refresh
    def refresh(self, secids: list[str] | None = None) -> dict[str, Any]:
        """Snapshots for all tracked bonds (or the given ones) + candles and DEMA of curve bonds."""
        assert self.iss is not None
        now = datetime.now(timezone.utc)
        market_now = (now - self.delay).astimezone(MSK)
        with self.sf() as s:
            stmt = select(Bond.secid, Bond.board).join(Issuer, Issuer.emitter_id == Bond.issuer_id).where(Bond.is_traded.is_(True))
            if secids:
                stmt = stmt.where(Bond.secid.in_(secids))
            bonds = s.execute(stmt).all()
            curve_secids = set(s.scalars(select(CurveBond.secid).distinct()))
        if secids:  # on-demand refresh of one curve: candles and DEMA of exactly these bonds
            curve_secids = set(secids)
        by_board: dict[str, set[str]] = {}
        for secid, board in bonds:
            if board:
                by_board.setdefault(board, set()).add(secid)

        stored, errors = 0, []
        for board, members in sorted(by_board.items()):
            try:
                data = self.iss.board_market(board)
            except IssError as exc:
                errors.append(f"{board}: {exc.detail}")
                continue
            with self.sf() as s, s.begin():
                for secid in members:
                    row = data.get(secid)
                    if not row:
                        continue
                    s.execute(pg_insert(BondSnapshot).values(
                        secid=secid,
                        fetched_at=now,
                        trade_date=market_now.date(),
                        market_time=market_now,
                        bid=_num(row.get("BID")),
                        ask=_num(row.get("OFFER")),
                        last=_num(row.get("LAST")),
                        vwap=_num(row.get("WAPRICE")),
                        num_trades=int(row.get("NUMTRADES") or 0),
                        turnover=_num(row.get("VALTODAY")),
                        face_value=_num(row.get("FACEVALUEONSETTLEDATE")) or _num(row.get("FACEVALUE")),
                        accrued=_num(row.get("ACCRUEDINT")),
                        settle_date=_date(row.get("SETTLEDATE")),
                    ).on_conflict_do_nothing())
                    stored += 1

        dema_info = self.refresh_candles_and_dema(sorted(curve_secids), stamp=now, store_auto=not secids) if curve_secids else {}
        return {"snapshots": stored, "boards": len(by_board), "curve_bonds": len(curve_secids), "dema": dema_info, "errors": errors}

    def refresh_candles_and_dema(self, secids: list[str], *, stamp: datetime | None = None, store_auto: bool = True) -> dict[str, Any]:
        assert self.iss is not None
        with self.sf() as s:
            cfg = settings_store.load(s)
            boards = dict(s.execute(select(Bond.secid, Bond.board).where(Bond.secid.in_(secids))).all())
            last_begin = dict(s.execute(
                select(BondCandle.secid, func.max(BondCandle.begin)).where(BondCandle.secid.in_(secids)).group_by(BondCandle.secid)
            ).all())
        lookback = int(cfg["dema"]["lookback_days"])
        today = datetime.now(MSK).date()
        for secid in secids:
            board = boards.get(secid)
            if not board:
                continue
            since = last_begin[secid].astimezone(MSK).date() if secid in last_begin else today - timedelta(days=lookback)
            try:
                rows = self.iss.candles(board, secid, since)
            except IssError as exc:
                log.warning("Candles of %s unavailable: %s", secid, exc)
                continue
            with self.sf() as s, s.begin():
                for r in rows:
                    begin = datetime.fromisoformat(str(r["begin"])).replace(tzinfo=MSK)
                    values = {"open": _num(r.get("open")), "close": _num(r.get("close")), "high": _num(r.get("high")),
                              "low": _num(r.get("low")), "turnover": _num(r.get("value")), "volume": _num(r.get("volume"))}
                    if values["close"] is None:
                        continue
                    s.execute(pg_insert(BondCandle).values(secid=secid, begin=begin, **values)
                              .on_conflict_do_update(index_elements=["secid", "begin"], set_=values))
        return self.update_dema(secids, stamp=stamp, store_auto=store_auto)

    def _candle_series(self, s: Session, secids: list[str], lookback: int, until: datetime | None = None) -> dict[str, tuple[np.ndarray, np.ndarray, list[datetime]]]:
        since = datetime.now(timezone.utc) - timedelta(days=lookback)
        stmt = select(BondCandle.secid, BondCandle.begin, BondCandle.close).where(
            BondCandle.secid.in_(secids), BondCandle.begin >= since
        )
        if until is not None:
            stmt = stmt.where(BondCandle.begin < until)
        rows = s.execute(stmt.order_by(BondCandle.secid, BondCandle.begin)).all()
        grouped: dict[str, list[tuple[datetime, float]]] = {}
        for secid, begin, close in rows:
            grouped.setdefault(secid, []).append((begin.astimezone(MSK).replace(tzinfo=None), close))
        out = {}
        for secid, items in grouped.items():
            begins = [b for b, _ in items]
            out[secid] = (np.array([c for _, c in items], dtype=float), trading_clock(begins), begins)
        return out

    def half_lives(self, s: Session, secids: list[str], series: dict[str, tuple[np.ndarray, np.ndarray, list[datetime]]]) -> tuple[dict[str, float], dict[str, Any]]:
        """Half-life per bond: manual override > global override > automatic choice."""
        cfg = settings_store.load(s)
        global_h = cfg["dema"].get("half_life_hours")
        manual = dict(s.execute(select(Bond.secid, Bond.manual).where(Bond.secid.in_(secids))).all())
        pooled, choices = choose_half_life({k: (v[0], v[1]) for k, v in series.items()})
        out = {}
        for secid in secids:
            own = (manual.get(secid) or {}).get("dema_half_life_hours")
            if own:
                out[secid] = float(own)
            elif global_h:
                out[secid] = float(global_h)
            else:
                out[secid] = choices[secid].half_life if secid in choices else pooled.half_life
        info = {
            "pooled_half_life_hours": pooled.half_life,
            "pooled_relative_mse": pooled.relative_mse,
            "bonds": {k: {"half_life": c.half_life, "acf1": c.acf1, "candles": c.n_candles, "pooled": c.pooled} for k, c in choices.items()},
        }
        return out, info

    def update_dema(self, secids: list[str], *, stamp: datetime | None = None, store_auto: bool = True) -> dict[str, Any]:
        with self.sf() as s, s.begin():
            cfg = settings_store.load(s)
            series = self._candle_series(s, secids, int(cfg["dema"]["lookback_days"]))
            halves, info = self.half_lives(s, secids, series)
            values = {}
            for secid, (prices, clock, begins) in series.items():
                if len(prices) == 0:
                    continue
                values[secid] = float(dema(prices, clock, halves[secid])[-1])
                if stamp is not None:
                    snap = s.get(BondSnapshot, (secid, stamp))
                    if snap is not None:
                        snap.dema, snap.dema_half_life = values[secid], halves[secid]
            if store_auto:
                settings_store.save(s, "dema_auto", {
                    "half_life_hours": info["pooled_half_life_hours"],
                    "relative_mse": info["pooled_relative_mse"],
                    "computed_at": datetime.now(timezone.utc).isoformat(),
                    "bonds": info["bonds"],
                }, SYSTEM, journal=False)
        return {"pooled_half_life_hours": info["pooled_half_life_hours"], "bonds": len(values)}

    def dema_series(self, secid: str) -> dict[str, Any]:
        """Hourly closes and DEMA for charts."""
        with self.sf() as s:
            cfg = settings_store.load(s)
            series = self._candle_series(s, [secid], int(cfg["dema"]["lookback_days"]))
            if secid not in series:
                return {"candles": [], "half_life": None}
            halves, info = self.half_lives(s, [secid], series)
        prices, clock, begins = series[secid]
        d = dema(prices, clock, halves[secid])
        return {
            "half_life": halves[secid],
            "auto": info["bonds"].get(secid),
            "candles": [{"begin": b.isoformat(), "close": float(c), "dema": float(v)} for b, c, v in zip(begins, prices, d)],
        }

    # ------------------------------------------------------------------ end of day
    def close_day(self, trade_date: date | None = None) -> dict[str, Any]:
        """Fix 19:00 values of ``trade_date`` (default: today) and of any earlier unclosed day."""
        target = trade_date or datetime.now(MSK).date()
        with self.sf() as s:
            dates = sorted(set(s.scalars(select(BondSnapshot.trade_date).distinct().where(BondSnapshot.trade_date <= target))))
        closed = {}
        for d in dates:
            closed[d.isoformat()] = self._close(d)
        with self.sf() as s, s.begin():
            purged = s.execute(delete(BondSnapshot).where(BondSnapshot.trade_date < target)).rowcount
            cfg = settings_store.load(s)
            # the day's bid-ask spread is in: refresh liquidity statistics without waiting for the morning scan
            secids = list(s.scalars(select(BondDaily.secid).where(BondDaily.trade_date == target, BondDaily.spread_pct.is_not(None))))
            recompute_liquidity(s, secids, int(cfg["liquidity"]["window_days"]), target)
            keep = datetime.now(timezone.utc) - timedelta(days=int(cfg["dema"]["lookback_days"]) + 10)
            old_candles = s.execute(delete(BondCandle).where(BondCandle.begin < keep)).rowcount
        return {"closed": closed, "purged_snapshots": purged, "purged_candles": old_candles}

    def _close(self, d: date) -> int:
        cutoff = datetime.combine(d, CLOSE_TIME, MSK)
        session_start = datetime.combine(d, SESSION_START, MSK)
        with self.sf() as s, s.begin():
            snaps = s.scalars(
                select(BondSnapshot).where(BondSnapshot.trade_date == d, BondSnapshot.market_time <= cutoff)
                .order_by(BondSnapshot.secid, BondSnapshot.market_time)
            ).all()
            by_bond: dict[str, list[BondSnapshot]] = {}
            for snap in snaps:
                by_bond.setdefault(snap.secid, []).append(snap)
            secids = list(by_bond)
            curve_secids = set(s.scalars(select(CurveBond.secid).distinct()))
            cfg = settings_store.load(s)
            series = self._candle_series(s, [x for x in secids if x in curve_secids], int(cfg["dema"]["lookback_days"]), until=cutoff)
            halves, _ = self.half_lives(s, list(series), series) if series else ({}, {})
            for secid, items in by_bond.items():
                def last_of(attr: str):
                    for it in reversed(items):
                        v = getattr(it, attr)
                        if v is not None:
                            return v
                    return None
                book = next((it for it in reversed(items) if it.bid and it.ask), None)
                spreads = [sp for it in items if it.market_time and it.market_time >= session_start
                           for sp in [spread_pct(it.bid, it.ask)] if sp is not None]
                values: dict[str, Any] = {
                    "bid": book.bid if book else last_of("bid"),
                    "ask": book.ask if book else last_of("ask"),
                    "last": last_of("last"),
                    "vwap": last_of("vwap"),
                    "eod_face_value": last_of("face_value"),
                    "eod_accrued": last_of("accrued"),
                    "eod_settle_date": last_of("settle_date"),
                    "eod_at": items[-1].market_time,
                    "spread_pct": statistics.median(spreads) if spreads else None,
                    "snapshots": len(items),
                }
                if secid in series and len(series[secid][0]):
                    prices, clock, _ = series[secid]
                    values["dema"] = float(dema(prices, clock, halves[secid])[-1])
                    values["dema_half_life"] = halves[secid]
                s.execute(pg_insert(BondDaily).values(secid=secid, trade_date=d, **values)
                          .on_conflict_do_update(index_elements=["secid", "trade_date"], set_=values))
        return len(by_bond)

    # ------------------------------------------------------------------ inputs of a calibration
    def observations(self, secids: list[str], as_of: date, *, live: bool) -> dict[str, Observation]:
        out = {secid: Observation(secid, source="none") for secid in secids}
        with self.sf() as s:
            bonds = {b.secid: b for b in s.scalars(select(Bond).where(Bond.secid.in_(secids)))}
            daily = {r.secid: r for r in s.scalars(select(BondDaily).where(BondDaily.secid.in_(secids), BondDaily.trade_date == as_of))}
            if live:
                latest = (
                    select(BondSnapshot.secid, func.max(BondSnapshot.fetched_at).label("ts"))
                    .where(BondSnapshot.secid.in_(secids), BondSnapshot.trade_date == as_of)
                    .group_by(BondSnapshot.secid).subquery()
                )
                snaps = {r.secid: r for r in s.scalars(
                    select(BondSnapshot).join(latest, (BondSnapshot.secid == latest.c.secid) & (BondSnapshot.fetched_at == latest.c.ts))
                )}
                cfg = settings_store.load(s)
                series = self._candle_series(s, secids, int(cfg["dema"]["lookback_days"]))
                halves, _ = self.half_lives(s, list(series), series) if series else ({}, {})
            for secid in secids:
                o = out[secid]
                bond = bonds.get(secid)
                day = daily.get(secid)
                if live:
                    snap = snaps.get(secid)
                    if snap is not None:
                        o.source = "intraday"
                        o.prices = {"bid": snap.bid, "ask": snap.ask, "last": snap.last, "vwap": snap.vwap}
                        o.face, o.accrued, o.settle_date = snap.face_value, snap.accrued, snap.settle_date
                        o.as_of = snap.market_time.astimezone(MSK).isoformat() if snap.market_time else None
                    if secid in series and len(series[secid][0]):
                        prices, clock, begins = series[secid]
                        o.prices["dema"] = float(dema(prices, clock, halves[secid])[-1])
                        o.dema_half_life = halves[secid]
                        o.last_candle = begins[-1].isoformat()
                        o.source = o.source if o.source != "none" else "candles"
                elif day is not None:
                    o.source = "eod"
                    o.prices = {"dema": day.dema, "bid": day.bid, "ask": day.ask,
                                "last": day.last if day.last is not None else day.close,
                                "vwap": day.vwap if day.vwap is not None else day.waprice}
                    o.dema_half_life = day.dema_half_life
                    o.face = day.eod_face_value or day.face_value
                    o.accrued = day.eod_accrued if day.eod_accrued is not None else day.accrued
                    o.settle_date = day.eod_settle_date
                    o.as_of = day.eod_at.astimezone(MSK).isoformat() if day.eod_at else as_of.isoformat()
                if o.face is None and bond is not None:
                    o.face = (day.face_value if day is not None and day.face_value else None) or bond.face_value
                if o.accrued is None and day is not None:
                    o.accrued = day.accrued
                if o.settle_date is None:
                    o.settle_date = next_weekday(as_of)
                o.prices = {k: v for k, v in o.prices.items() if v is not None}
        return out
