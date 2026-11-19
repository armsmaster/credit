"""DEMA of hourly closes with an automatically chosen half-life.

Clock. Candles of illiquid bonds are irregular, so the decay runs on a trading clock: hours
between 07:00 and 24:00 Moscow time on weekdays (nights and weekends do not age the level).
For a step of Δ trading hours and half-life h:  α = 1 − 2^(−Δ/h).

    E1_t = E1_{t−1} + α_t (x_t − E1_{t−1})
    E2_t = E2_{t−1} + α_t (E1_t − E2_{t−1})
    DEMA_t = 2·E1_t − E2_t

Half-life. Model the close as a latent market level plus noise, x_t = μ_t + ε_t. A filter that
is too fast follows the noise, and the noise then reverts — its one-step-ahead errors
u_t = x_t − DEMA_{t−1} are negatively autocorrelated (mean reversion). A filter that is too slow
lags behind μ — the errors keep the same sign for a while (positive autocorrelation).
The half-life that minimises the mean squared one-step-ahead error balances the two; at that
point the errors are close to white noise (their lag-1 autocorrelation is reported as a check).
Errors are normalised by each bond's own variance of price changes, so several bonds can be
pooled into one default half-life.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

DAY_START_HOUR = 7
HOURS_PER_DAY = 17  # 07:00 - 24:00
HALF_LIFE_GRID = np.unique(np.round(np.geomspace(1.0, 240.0, 40), 2))
MIN_CANDLES = 60  # fewer candles: use the pooled default instead of a bond's own optimum
BURN_IN = 5


def trading_clock(begins: list[datetime]) -> np.ndarray:
    """Trading-hour coordinate of each candle start (weekday hours 07:00-24:00 only)."""
    if not begins:
        return np.zeros(0)
    days = np.array([b.date() for b in begins], dtype="datetime64[D]")
    busday = np.busday_count(days.min(), days)
    hours = np.array([min(max(b.hour + b.minute / 60.0 - DAY_START_HOUR, 0.0), HOURS_PER_DAY - 1) for b in begins])
    return busday * HOURS_PER_DAY + hours


def dema(prices: np.ndarray, clock: np.ndarray, half_life: float) -> np.ndarray:
    prices = np.asarray(prices, dtype=float)
    out = np.empty_like(prices)
    if len(prices) == 0:
        return out
    e1 = e2 = prices[0]
    out[0] = prices[0]
    for i in range(1, len(prices)):
        dt = max(clock[i] - clock[i - 1], 1.0)  # at least one hour between consecutive candles
        a = 1.0 - 2.0 ** (-dt / half_life)
        e1 += a * (prices[i] - e1)
        e2 += a * (e1 - e2)
        out[i] = 2.0 * e1 - e2
    return out


def _errors(prices: np.ndarray, clock: np.ndarray, half_life: float) -> np.ndarray:
    d = dema(prices, clock, half_life)
    return prices[1:] - d[:-1]


@dataclass
class HalfLifeChoice:
    half_life: float
    acf1: float | None  # lag-1 autocorrelation of one-step errors at the optimum
    relative_mse: float | None  # MSE / variance of price changes at the optimum
    n_candles: int
    pooled: bool


def _profile(prices: np.ndarray, clock: np.ndarray) -> np.ndarray | None:
    """Normalised one-step MSE for every half-life of the grid (None if too little data)."""
    if len(prices) < BURN_IN + 10:
        return None
    scale = np.var(np.diff(prices))
    if scale <= 0:
        return None
    return np.array([np.mean(_errors(prices, clock, h)[BURN_IN:] ** 2) / scale for h in HALF_LIFE_GRID])


def _acf1(x: np.ndarray) -> float | None:
    x = x - x.mean()
    denom = float(x @ x)
    return float(x[1:] @ x[:-1]) / denom if denom > 0 else None


def choose_half_life(series: dict[str, tuple[np.ndarray, np.ndarray]], fallback: float = 8.0) -> tuple[HalfLifeChoice, dict[str, HalfLifeChoice]]:
    """Pooled default half-life over all series and the per-series choice.

    A series with at least MIN_CANDLES candles gets its own optimum, others use the pooled one.
    """
    profiles = {k: _profile(p, c) for k, (p, c) in series.items()}
    valid = [v for v in profiles.values() if v is not None]
    if valid:
        pooled_curve = np.mean(valid, axis=0)
        pooled_h = float(HALF_LIFE_GRID[int(np.argmin(pooled_curve))])
        pooled = HalfLifeChoice(pooled_h, None, float(pooled_curve.min()), sum(len(series[k][0]) for k, v in profiles.items() if v is not None), True)
    else:
        pooled = HalfLifeChoice(fallback, None, None, 0, True)

    choices: dict[str, HalfLifeChoice] = {}
    for key, (prices, clock) in series.items():
        prof = profiles[key]
        if prof is not None and len(prices) >= MIN_CANDLES:
            i = int(np.argmin(prof))
            h, rel, is_pooled = float(HALF_LIFE_GRID[i]), float(prof[i]), False
        else:
            h, rel, is_pooled = pooled.half_life, None, True
        acf = _acf1(_errors(prices, clock, h)[BURN_IN:]) if len(prices) > BURN_IN + 3 else None
        choices[key] = HalfLifeChoice(h, acf, rel, len(prices), is_pooled)
    return pooled, choices
