"""Pricing of a defaultable bond on a piecewise-flat hazard-rate curve.

Hazard rate λ(t) is constant inside every calendar month from the curve date D up to 10 years
(120 segments) and flat beyond. Survival Q(t) = exp(-∫λ). With recovery R of the outstanding
notional paid at default (recovery of face, the market convention for bonds):

    PV(D) = Σ_i CF_i · DF(t_i) · Q(t_i)                                   (promised cash flows)
          + R · Σ_j N(u_{j-1}) · DF(ū_j) · (Q(u_{j-1}) − Q(u_j))          (default leg)

where u_j is a grid from settlement S to the effective maturity (month ends + cash-flow dates).
The model dirty price per bond at settlement, conditional on survival to S, is
P = PV / (DF(S)·Q(S)); clean price in % = (P − accrued) / N(S) · 100.

Everything that does not depend on λ is precomputed in BondPricer, so price and its gradient
for any λ are a couple of matrix products (fast analytic Jacobian for the calibration).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
from dateutil.relativedelta import relativedelta

from .cashflows import ProjectedBond
from .rates import RateCurve

HORIZON_MONTHS = 120
DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class HazardGrid:
    curve_date: date
    knot_dates: list[date]  # D, D+1M, ..., D+120M
    knots: np.ndarray  # year fractions ACT/365F

    @classmethod
    def monthly(cls, curve_date: date, months: int = HORIZON_MONTHS) -> "HazardGrid":
        dates = [curve_date + relativedelta(months=k) for k in range(months + 1)]
        knots = np.array([(d - curve_date).days / DAYS_PER_YEAR for d in dates])
        return cls(curve_date, dates, knots)

    @property
    def size(self) -> int:
        return len(self.knots) - 1

    def exposure(self, t: np.ndarray) -> np.ndarray:
        """A[i, k] = time spent in hazard segment k before t_i (years); the last segment is open-ended."""
        t = np.asarray(t, dtype=float).reshape(-1, 1)
        lo = self.knots[:-1].reshape(1, -1)
        hi = self.knots[1:].copy()
        hi[-1] = np.inf
        return np.clip(np.minimum(t, hi.reshape(1, -1)) - lo, 0.0, None)

    def month_index(self, d: date) -> int:
        """Number of whole segments needed to cover date d (1..size)."""
        k = int(np.searchsorted(self.knots, (d - self.curve_date).days / DAYS_PER_YEAR, side="left"))
        return min(max(k, 1), self.size)


class BondPricer:
    def __init__(
        self,
        bond: ProjectedBond,
        grid: HazardGrid,
        discount: RateCurve,
        recovery: float,
        accrued: float,
    ):
        self.bond = bond
        self.grid = grid
        self.recovery = recovery
        self.accrued = accrued
        self.face = bond.face_settle
        D = bond.curve_date
        s_days = (bond.settle_date - D).days
        m_days = (bond.maturity - D).days

        cf_days = bond.days
        self.cf_t = cf_days / DAYS_PER_YEAR
        self.cf_df = discount.df_days(D, cf_days)
        self.cf_amount = bond.amounts
        self.cf_c = self.cf_amount * self.cf_df

        # default grid: settlement, month knots and cash-flow dates up to maturity
        knot_days = np.array([(d - D).days for d in grid.knot_dates], dtype=float)
        pts = np.concatenate([[s_days, m_days], knot_days[(knot_days > s_days) & (knot_days < m_days)], cf_days[cf_days < m_days]])
        u = np.unique(pts[(pts >= s_days) & (pts <= m_days)])
        self.dg_t = u / DAYS_PER_YEAR
        mid = 0.5 * (u[1:] + u[:-1])
        self.dg_w = recovery * bond.notional_at(u[:-1]) * discount.df_days(D, mid)

        self.t_s = s_days / DAYS_PER_YEAR
        self.df_s = float(discount.df_days(D, s_days))
        self.A_cf = grid.exposure(self.cf_t)
        self.A_dg = grid.exposure(self.dg_t)
        self.A_s = grid.exposure(np.array([self.t_s]))[0]

    # -- price ---------------------------------------------------------------------
    def dirty(self, hazard: np.ndarray) -> float:
        q_cf = np.exp(-self.A_cf @ hazard)
        q_dg = np.exp(-self.A_dg @ hazard)
        q_s = math.exp(-float(self.A_s @ hazard))
        num = float(self.cf_c @ q_cf + self.dg_w @ (q_dg[:-1] - q_dg[1:]))
        return num / (self.df_s * q_s)

    def clean_pct(self, hazard: np.ndarray) -> float:
        return (self.dirty(hazard) - self.accrued) / self.face * 100.0

    def clean_pct_and_grad(self, hazard: np.ndarray) -> tuple[float, np.ndarray]:
        """Clean price in % and its gradient with respect to the hazard rates (decimal units)."""
        q_cf = np.exp(-self.A_cf @ hazard)
        q_dg = np.exp(-self.A_dg @ hazard)
        q_s = math.exp(-float(self.A_s @ hazard))
        num = float(self.cf_c @ q_cf + self.dg_w @ (q_dg[:-1] - q_dg[1:]))
        d_num = -(self.cf_c * q_cf) @ self.A_cf
        w_q = self.dg_w[:, None]
        d_num += (w_q * (-q_dg[:-1, None] * self.A_dg[:-1] + q_dg[1:, None] * self.A_dg[1:])).sum(axis=0)
        dirty = num / (self.df_s * q_s)
        d_dirty = d_num / (self.df_s * q_s) + dirty * self.A_s
        return (dirty - self.accrued) / self.face * 100.0, d_dirty / self.face * 100.0

    # -- sensitivities and spreads ---------------------------------------------------
    def spread_duration(self, hazard: np.ndarray) -> float:
        """Price change in % of notional for a 1 bp parallel shift of the discount rate (> 0)."""
        q_cf = np.exp(-self.A_cf @ hazard)
        q_dg = np.exp(-self.A_dg @ hazard)
        q_s = math.exp(-float(self.A_s @ hazard))
        dg_mid_t = 0.5 * (self.dg_t[1:] + self.dg_t[:-1])
        weighted = float((self.cf_c * q_cf) @ (self.cf_t - self.t_s)) + float((self.dg_w * (q_dg[:-1] - q_dg[1:])) @ (dg_mid_t - self.t_s))
        return weighted / (self.df_s * q_s) / self.face * 100.0 * 1e-4

    def z_spread(self, clean_pct: float) -> float | None:
        """Z-spread (bp, continuous) of promised cash flows over the discount curve for a clean price."""
        target = clean_pct / 100.0 * self.face + self.accrued
        tau = self.cf_t - self.t_s
        base = self.cf_c / self.df_s
        z = 0.0
        for _ in range(60):
            disc = np.exp(-z * tau)
            value = float(base @ disc) - target
            deriv = -float((base * tau) @ disc)
            if deriv == 0:
                return None
            step = value / deriv
            z -= step
            if abs(step) < 1e-10:
                return z * 1e4
        return None
