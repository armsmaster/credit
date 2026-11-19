"""Risk-free / projection curves received from the interest-rate curves service.

The curves service stores a curve as monthly nodes (date, DF) with log-linear interpolation of
discount factors (piecewise-flat forwards) and a flat last forward beyond the last node.
We reproduce exactly that interpolation, evaluated on day offsets from the CREDIT curve date:

    DF_D(d) = DF(d) / DF(D)

so an interest-rate curve built on an earlier date (e.g. not rebuilt yet today) is rolled
forward to the credit curve date D along its own forwards.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RateCurve:
    code: str
    as_of: date  # date of the interest-rate curve itself
    basis: float  # 365 (ACT/365F) or 360 (ACT/360)
    times: np.ndarray  # years from as_of, times[0] == 0
    log_dfs: np.ndarray  # ln DF at times

    @classmethod
    def from_snapshot(cls, code: str, as_of: date, day_count: str, nodes: list[dict[str, Any]]) -> "RateCurve":
        basis = 360.0 if str(day_count).upper().endswith("360") else 365.0
        pts = sorted(((date.fromisoformat(str(n["date"])), float(n["df"])) for n in nodes), key=lambda p: p[0])
        times = np.array([(d - as_of).days / basis for d, _ in pts], dtype=float)
        log_dfs = np.log(np.array([df for _, df in pts], dtype=float))
        if len(times) == 0 or times[0] > 0:
            times = np.concatenate([[0.0], times])
            log_dfs = np.concatenate([[0.0], log_dfs])
        keep = np.concatenate([[True], np.diff(times) > 0])
        times, log_dfs = times[keep], log_dfs[keep]
        if len(times) < 2:
            raise ValueError(f"Кривая {code}: недостаточно узлов")
        return cls(code, as_of, basis, times, log_dfs)

    @classmethod
    def flat(cls, code: str, as_of: date, rate: float) -> "RateCurve":
        """Flat continuously compounded curve (tests)."""
        times = np.array([0.0, 50.0])
        return cls(code, as_of, 365.0, times, -rate * times)

    def _log_df_t(self, t: np.ndarray) -> np.ndarray:
        times, ldf = self.times, self.log_dfs
        last_fwd = -(ldf[-1] - ldf[-2]) / (times[-1] - times[-2])
        inside = np.interp(t, times, ldf)
        beyond = ldf[-1] - last_fwd * (t - times[-1])
        out = np.where(t > times[-1], beyond, inside)
        first_fwd = -(ldf[1] - ldf[0]) / (times[1] - times[0])
        return np.where(t < 0, -first_fwd * t, out)  # before the curve date: extrapolate the first forward

    def df_days(self, curve_date: date, days: np.ndarray | float) -> np.ndarray:
        """DF from ``curve_date`` (the credit curve date) to curve_date + days."""
        base = (curve_date - self.as_of).days / self.basis
        t = base + np.asarray(days, dtype=float) / self.basis
        return np.exp(self._log_df_t(t) - self._log_df_t(np.array(base)))

    def simple_forward(self, curve_date: date, start_days: float, end_days: float) -> float:
        """Simple ACT/365 rate between two day offsets (compounded overnight index forward)."""
        if end_days <= start_days:
            end_days = start_days + 1
        d1, d2 = self.df_days(curve_date, np.array([start_days, end_days]))
        return float((d1 / d2 - 1.0) * 365.0 / (end_days - start_days))
