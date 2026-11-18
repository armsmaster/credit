"""Discount curve on a monthly grid with log-linear interpolation of discount factors.

ln DF(t) is piecewise linear between nodes  <=>  the instantaneous forward rate is
constant inside every month. Beyond the last node the last forward is extrapolated flat.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum

import numpy as np

from .conventions import DayCount


class Compounding(str, Enum):
    CONTINUOUS = "continuous"
    ANNUAL = "annual"
    SEMIANNUAL = "semiannual"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"

    @property
    def periods(self) -> int | None:
        return {
            Compounding.CONTINUOUS: None,
            Compounding.ANNUAL: 1,
            Compounding.SEMIANNUAL: 2,
            Compounding.QUARTERLY: 4,
            Compounding.MONTHLY: 12,
        }[self]


def rate_from_df(df: float, t: float, comp: Compounding) -> float:
    """Rate (decimal) that discounts 1 to ``df`` over ``t`` years."""
    if t <= 0:
        raise ValueError("t must be positive")
    m = comp.periods
    if m is None:
        return -math.log(df) / t
    return m * (df ** (-1.0 / (m * t)) - 1.0)


def df_from_rate(rate: float, t: float, comp: Compounding) -> float:
    m = comp.periods
    if m is None:
        return math.exp(-rate * t)
    return (1.0 + rate / m) ** (-m * t)


@dataclass(frozen=True)
class DiscountCurve:
    as_of: date
    day_count: DayCount
    times: np.ndarray  # year fractions of nodes, times[0] == 0
    log_dfs: np.ndarray  # ln DF at nodes, log_dfs[0] == 0

    @classmethod
    def from_nodes(cls, as_of: date, day_count: DayCount, dates: list[date], dfs: list[float]) -> "DiscountCurve":
        times = np.array([day_count.year_fraction(as_of, d) for d in dates], dtype=float)
        log_dfs = np.log(np.array(dfs, dtype=float))
        if times[0] != 0.0:
            times = np.concatenate([[0.0], times])
            log_dfs = np.concatenate([[0.0], log_dfs])
        if np.any(np.diff(times) <= 0):
            raise ValueError("Узлы кривой должны строго возрастать по времени")
        return cls(as_of, day_count, times, log_dfs)

    def t(self, d: date) -> float:
        return self.day_count.year_fraction(self.as_of, d)

    def df_t(self, t: float) -> float:
        if t <= 0:
            return 1.0
        times, ldf = self.times, self.log_dfs
        if t >= times[-1]:
            last_fwd = -(ldf[-1] - ldf[-2]) / (times[-1] - times[-2])
            return math.exp(ldf[-1] - last_fwd * (t - times[-1]))
        return math.exp(float(np.interp(t, times, ldf)))

    def df(self, d: date) -> float:
        return self.df_t(self.t(d))

    def zero_rate(self, d: date, comp: Compounding) -> float:
        t = self.t(d)
        if t <= 0:
            return self.instantaneous_forward(d)
        return rate_from_df(self.df_t(t), t, comp)

    def forward_rate(self, start: date, end: date, comp: Compounding) -> float:
        t1, t2 = self.t(start), self.t(end)
        if t2 <= t1:
            raise ValueError("Дата окончания должна быть позже даты начала")
        return rate_from_df(self.df_t(t2) / self.df_t(t1), t2 - t1, comp)

    def instantaneous_forward(self, d: date) -> float:
        """Continuous instantaneous forward at date d (constant within a month)."""
        t = max(self.t(d), 0.0)
        times, ldf = self.times, self.log_dfs
        k = int(np.searchsorted(times, t, side="right"))
        k = min(max(k, 1), len(times) - 1)
        return -(ldf[k] - ldf[k - 1]) / (times[k] - times[k - 1])
