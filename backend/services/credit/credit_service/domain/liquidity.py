"""Liquidity metrics of a bond over a trailing window of trading days.

* average daily turnover (RUB) and average number of trades — over all trading days of the
  window since the bond started trading, days without trades count as zero;
* 20th / 80th percentiles of the daily turnover — only if the bond has traded for more than a
  month (≥ 21 trading days in the window), otherwise not meaningful;
* average bid-ask spread, % of mid — over days with our own order-book snapshots (ISS history
  has no bid/ask, the service accumulates it from intraday snapshots).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

MIN_DAYS_FOR_PERCENTILES = 21


def liquidity_metrics(
    days: list[tuple[date, float | None, int | None]],
    spreads: list[float],
    *,
    window_days: int,
    as_of: date,
) -> dict[str, Any]:
    rows = [(d, v or 0.0, n or 0) for d, v, n in days if (as_of - d).days <= window_days]
    rows.sort()
    out: dict[str, Any] = {
        "window_days": window_days,
        "trading_days": len(rows),
        "days_with_trades": sum(1 for _, v, n in rows if n > 0),
        "avg_turnover": None,
        "p20_turnover": None,
        "p80_turnover": None,
        "avg_trades": None,
        "last_trade_date": None,
        "avg_spread_pct": float(np.mean(spreads)) if spreads else None,
        "spread_days": len(spreads),
    }
    if not rows:
        return out
    values = np.array([v for _, v, _ in rows])
    trades = np.array([n for _, _, n in rows])
    out["avg_turnover"] = float(values.mean())
    out["avg_trades"] = float(trades.mean())
    traded = [d for d, _, n in rows if n > 0]
    out["last_trade_date"] = traded[-1].isoformat() if traded else None
    if len(rows) >= MIN_DAYS_FOR_PERCENTILES:
        out["p20_turnover"] = float(np.percentile(values, 20))
        out["p80_turnover"] = float(np.percentile(values, 80))
    return out


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    if not bid or not ask or ask < bid:
        return None
    mid = 0.5 * (bid + ask)
    return (ask - bid) / mid * 100.0 if mid > 0 else None
