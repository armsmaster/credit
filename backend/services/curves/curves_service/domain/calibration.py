"""Smooth-forward calibration of an OIS curve to illiquid par quotes.

Unknowns: piecewise-constant continuous forwards f_j on a grid of monthly nodes
(plus extra nodes at maturities of quotes that fall between months, e.g. 1W/2W).

    minimise  J(f) = FIT + κ·BIDASK + λ·ROUGH + μ·FLAT

    FIT    = 1/n Σ_i e_i²                       e_i = S_i(f) − mid_i            [bp]
    BIDASK = 1/n Σ_i ((|e_i| − h_i)_+)²         h_i = half bid/ask spread       [bp]
    ROUGH  = 1/L ∫ ω(t)·f''(t)² dt              curvature of forwards, t in months
    FLAT   = 1/L_flat ∫_{t > T_flat} f'(t)² dt   slope of the long end

    ω(t) = w_s on the short end (t < T_s), ramps linearly to 1 at 2·T_s
    (variable roughness penalty, Waggoner 1997: the policy-rate path makes the
    short end genuinely curved; the long end must be smooth).

    s.t.   |e_i| ≤ D_max                (per-quote limit)
           1/n Σ_i |e_i| ≤ D_avg        (average limit)

λ (``smoothness``) is the single knob that makes the forward curve smoother.

Solution method (Gauss–Newton in the space of mid adjustments):
  1. linearise par rates around the current forwards: S(f) ≈ S0 + J (f − f0);
  2. for ANY vector of adjusted mids the smoothest curve through them is the
     closed-form KKT solution of  min f'Qf  s.t.  J f = b  (maximum smoothness);
     hence the smoothness cost is a quadratic form of the n adjustments;
  3. choose the n adjustments (a tiny convex problem with the hard limits);
  4. repeat until the forwards stop changing.
See docs/curves-math.md for the rationale.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np
from scipy.optimize import minimize

from .conventions import SwapConventions, add_months, months_to_cover
from .curve import DiscountCurve
from .quotes import Quote

BP = 100.0  # percent -> bp
_SMOOTH_ABS = 0.01  # bp, smoothing of |e| inside the average-deviation limit
_TOL_BP = 0.01  # accepted violation of hard limits


@dataclass(frozen=True)
class CalibrationParams:
    max_avg_deviation_bp: float = 5.0
    max_point_deviation_bp: float = 15.0
    smoothness: float = 10.0
    bid_ask_penalty: float = 10.0
    long_end_flat_from_months: int | None = None
    long_end_flatness: float = 10.0
    short_end_months: int = 12
    short_end_weight: float = 0.1

    def with_overrides(self, overrides: dict[str, Any] | None) -> "CalibrationParams":
        if not overrides:
            return self
        known = {k: v for k, v in overrides.items() if k in self.__dataclass_fields__}
        return replace(self, **known)

    def validate(self) -> None:
        if self.max_avg_deviation_bp < 0 or self.max_point_deviation_bp < 0:
            raise ValueError("Допустимые отклонения не могут быть отрицательными")
        if self.smoothness < 0 or self.bid_ask_penalty < 0 or self.long_end_flatness < 0:
            raise ValueError("Веса штрафов не могут быть отрицательными")
        if self.long_end_flat_from_months is not None and self.long_end_flat_from_months < 1:
            raise ValueError("Начало плоского участка — не раньше 1M")
        if not 0 < self.short_end_weight <= 1:
            raise ValueError("Вес гладкости короткого конца — в диапазоне (0; 1]")
        if self.short_end_months < 0:
            raise ValueError("Длина короткого конца не может быть отрицательной")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FittedQuote:
    tenor: str
    maturity: date
    t: float
    bid: float
    ask: float
    mid: float
    model: float  # adjusted ("fitted") mid, percent
    deviation_bp: float
    half_spread_bp: float
    in_spread: bool


@dataclass
class CurveNode:
    months: int | None  # None for extra (non-monthly) nodes
    date: date
    t: float
    df: float
    zero: float  # continuous zero rate, percent
    forward: float | None  # continuous forward on the segment ending at this node, percent
    par: float | None  # par rate of a swap maturing at this node, percent


@dataclass
class CalibrationResult:
    as_of: date
    params: CalibrationParams
    nodes: list[CurveNode]
    fitted: list[FittedQuote]
    stats: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def curve(self, conventions: SwapConventions) -> DiscountCurve:
        return DiscountCurve.from_nodes(
            self.as_of, conventions.day_count, [n.date for n in self.nodes], [n.df for n in self.nodes]
        )


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
@dataclass
class _InstrumentSpec:
    single: bool
    idx_maturity: int
    idx_payments: np.ndarray
    accruals: np.ndarray


class _ParPricer:
    """Par rates S(x) [%] and Jacobian dS/dx; x = segment forwards in percent."""

    def __init__(self, as_of: date, grid_t: np.ndarray, quotes: list[Quote], conv: SwapConventions):
        dc = conv.day_count
        times: list[float] = []
        index: dict[date, int] = {}

        def idx(d: date) -> int:
            if d not in index:
                index[d] = len(times)
                times.append(dc.year_fraction(as_of, d))
            return index[d]

        self.specs: list[_InstrumentSpec] = []
        for q in quotes:
            pays = conv.payment_dates(as_of, q.tenor)
            prev = [as_of] + pays[:-1]
            accr = np.array([dc.year_fraction(a, b) for a, b in zip(prev, pays)])
            self.specs.append(_InstrumentSpec(len(pays) == 1, idx(pays[-1]), np.array([idx(p) for p in pays]), accr))
        self.A = _overlap_matrix(np.array(times), grid_t)

    def __call__(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        df = np.exp(-(self.A @ x) / 100.0)
        d_df = -(df[:, None] * self.A) / 100.0
        s = np.empty(len(self.specs))
        jac = np.empty((len(self.specs), len(x)))
        for i, sp in enumerate(self.specs):
            iT = sp.idx_maturity
            if sp.single:
                tau = sp.accruals[0]
                s[i] = (1.0 / df[iT] - 1.0) / tau
                jac[i] = -d_df[iT] / (df[iT] ** 2 * tau)
            else:
                num = 1.0 - df[iT]
                ann = sp.accruals @ df[sp.idx_payments]
                d_ann = sp.accruals @ d_df[sp.idx_payments]
                s[i] = num / ann
                jac[i] = (-d_df[iT] * ann - num * d_ann) / ann**2
        return s * 100.0, jac * 100.0


def _overlap_matrix(times: np.ndarray, grid_t: np.ndarray) -> np.ndarray:
    """A[m, j] = length of [0, times[m]] ∩ segment j, so ln DF(t_m) = −Σ_j A[m, j]·f_j."""
    lo, dt = grid_t[:-1], np.diff(grid_t)
    a = np.clip(times[:, None] - lo[None, :], 0.0, dt[None, :])
    a[:, -1] = np.maximum(times - lo[-1], 0.0)  # flat extrapolation beyond the last node
    return a


def _roughness_weight(months: float, short_end_months: int, short_end_weight: float) -> float:
    if short_end_months <= 0 or months >= 2 * short_end_months:
        return 1.0
    if months <= short_end_months:
        return short_end_weight
    return short_end_weight + (1.0 - short_end_weight) * (months - short_end_months) / short_end_months


def _curvature_operator(
    grid_t: np.ndarray, short_end_months: int = 0, short_end_weight: float = 1.0
) -> tuple[np.ndarray, np.ndarray, float]:
    """Rows r with Σ (r·f)² ≈ ∫ ω(t) f''² dt (t in months) for piecewise-constant f.

    Returns weighted rows, unweighted rows (for reporting) and the total length in months.
    """
    c = 6.0 * (grid_t[:-1] + grid_t[1:])  # segment midpoints in months
    n = len(c)
    rows = np.zeros((max(n - 2, 0), n))
    weights = np.ones(max(n - 2, 0))
    for j in range(1, n - 1):
        a, b = 1.0 / (c[j] - c[j - 1]), 1.0 / (c[j + 1] - c[j])
        w = 0.5 * (c[j + 1] - c[j - 1])
        rows[j - 1, j - 1 : j + 2] = np.array([a, -(a + b), b]) / np.sqrt(w)
        weights[j - 1] = _roughness_weight(c[j], short_end_months, short_end_weight)
    return rows * np.sqrt(weights)[:, None], rows, 12.0 * grid_t[-1]


def _slope_operator(grid_t: np.ndarray, from_t: float) -> tuple[np.ndarray, float]:
    """Rows r with Σ (r·f)² ≈ ∫_{t > from_t} f'² dt (t in months)."""
    c = 6.0 * (grid_t[:-1] + grid_t[1:])
    rows, length = [], 0.0
    for j in range(1, len(c)):
        if grid_t[j] + 1e-12 < from_t:  # node between segments j-1 and j
            continue
        gap = c[j] - c[j - 1]
        r = np.zeros(len(c))
        r[j - 1], r[j] = -1.0 / np.sqrt(gap), 1.0 / np.sqrt(gap)
        rows.append(r)
        length += gap
    return (np.array(rows) if rows else np.zeros((0, len(c)))), length


def _initial_forwards(as_of: date, quotes: list[Quote], conv: SwapConventions, grid_t: np.ndarray) -> np.ndarray:
    """Rough start: approximate zero rates at quote maturities, linear in t, then forwards."""
    dc = conv.day_count
    qt, qz = [], []
    for q in quotes:
        t = dc.year_fraction(as_of, q.tenor.maturity(as_of))
        s = q.mid / 100.0
        if len(conv.payment_dates(as_of, q.tenor)) == 1:
            z = np.log1p(s * t) / t
        else:
            p = conv.fixed_frequency_months / 12.0
            z = np.log1p(s * p) / p
        qt.append(t)
        qz.append(z)
    zt = np.interp(grid_t, qt, qz) * grid_t
    return np.diff(zt) / np.diff(grid_t) * 100.0


def resolve_flat_from(quotes: list[Quote], as_of: date, params: CalibrationParams) -> int | None:
    if params.long_end_flat_from_months is not None:
        return params.long_end_flat_from_months
    if len(quotes) < 2:
        return None
    return months_to_cover(as_of, quotes[-2].tenor.maturity(as_of))


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def calibrate(
    as_of: date,
    quotes: list[Quote],
    conventions: SwapConventions,
    params: CalibrationParams,
) -> CalibrationResult:
    params.validate()
    started = time.perf_counter()
    dc = conventions.day_count
    n = len(quotes)

    # Grid: monthly nodes + maturities of quotes that fall between months.
    maturities = [q.tenor.maturity(as_of) for q in quotes]
    n_months = max(months_to_cover(as_of, m) for m in maturities)
    monthly = {add_months(as_of, k): k for k in range(n_months + 1)}
    grid_dates = sorted(set(monthly) | set(maturities))
    grid_t = np.array([dc.year_fraction(as_of, d) for d in grid_dates])
    n_seg = len(grid_dates) - 1

    pricer = _ParPricer(as_of, grid_t, quotes, conventions)
    mid = np.array([q.mid for q in quotes])
    half = np.array([q.half_spread_bp for q in quotes])

    flat_from = resolve_flat_from(quotes, as_of, params)
    eff_params = replace(params, long_end_flat_from_months=flat_from)

    rough_op, rough_raw, length = _curvature_operator(grid_t, params.short_end_months, params.short_end_weight)
    if flat_from is not None and flat_from < n_months:
        flat_op, flat_len = _slope_operator(grid_t, dc.year_fraction(as_of, add_months(as_of, flat_from)))
    else:
        flat_op, flat_len = np.zeros((0, n_seg)), 0.0

    d_max, d_avg = params.max_point_deviation_bp, params.max_avg_deviation_bp
    kappa = params.bid_ask_penalty

    # Quadratic smoothness form: x'Qx = λ·ROUGH + μ·FLAT (+ a tiny global slope term that
    # only matters when the data cannot pin the curve, e.g. a single quote).
    slope_all, _ = _slope_operator(grid_t, 0.0)
    q_mat = (BP**2) * (
        (params.smoothness / length) * (rough_op.T @ rough_op)
        + ((params.long_end_flatness / flat_len) * (flat_op.T @ flat_op) if len(flat_op) else 0.0)
        + (1e-6 / length) * (slope_all.T @ slope_all)
    )

    x = _initial_forwards(as_of, quotes, conventions, grid_t)
    e_star = np.zeros(n)
    converged, outer = False, 0
    for outer in range(1, 31):
        s0, js = pricer(x)
        b0 = mid - s0 + js @ x  # target model rates (linearised) = b0 + e/BP
        # Smoothest curve through given model rates: KKT of  min x'Qx  s.t.  J x = b.
        kkt = np.block([[2.0 * q_mat, js.T], [js, np.zeros((n, n))]])
        basis = np.linalg.solve(kkt, np.vstack([np.zeros((n_seg, n)), np.eye(n)]))[:n_seg]
        p_mat = basis.T @ q_mat @ basis  # smoothness cost as a quadratic form of model rates
        e_star = _solve_adjustments(b0, p_mat, half, kappa, d_max, d_avg, e_star)
        x_new = basis @ (b0 + e_star / BP)
        step = float(np.abs(x_new - x).max())
        x = x_new
        if step < 1e-6:  # forwards stable to 0.0001 bp
            converged = True
            break

    s_fit, _ = pricer(x)
    e = (s_fit - mid) * BP
    abs_e = np.abs(e)
    over = np.maximum(abs_e - half, 0.0)
    curv_w = rough_op @ x * BP
    curv = rough_raw @ x * BP  # unweighted, for reporting
    slope = flat_op @ x * BP
    fit_term = float(e @ e / n)
    ba_term = float(over @ over / n)
    rough_term = float(curv_w @ curv_w / length) if len(curv_w) else 0.0
    flat_term = float(slope @ slope / flat_len) if len(slope) else 0.0

    # ---- result ---------------------------------------------------------
    log_df = np.concatenate([[0.0], -np.cumsum(x / 100.0 * np.diff(grid_t))])
    dfs = np.exp(log_df)
    par = _par_rates(as_of, grid_dates, monthly, dfs, conventions)
    nodes = [
        CurveNode(
            months=monthly.get(d),
            date=d,
            t=float(grid_t[j]),
            df=float(dfs[j]),
            zero=float(-log_df[j] / grid_t[j] * 100.0) if j > 0 else float(x[0]),
            forward=float(x[j - 1]) if j > 0 else None,
            par=par[j],
        )
        for j, d in enumerate(grid_dates)
    ]
    fitted = [
        FittedQuote(
            tenor=str(q.tenor),
            maturity=maturities[i],
            t=dc.year_fraction(as_of, maturities[i]),
            bid=q.bid,
            ask=q.ask,
            mid=q.mid,
            model=float(s_fit[i]),
            deviation_bp=float(e[i]),
            half_spread_bp=float(half[i]),
            in_spread=bool(q.bid - 1e-9 <= s_fit[i] <= q.ask + 1e-9),
        )
        for i, q in enumerate(quotes)
    ]
    stats = {
        "mean_abs_deviation_bp": float(abs_e.mean()),
        "max_abs_deviation_bp": float(abs_e.max()),
        "rms_deviation_bp": float(np.sqrt(fit_term)),
        "outside_spread": int(sum(not f.in_spread for f in fitted)),
        "roughness": float(curv @ curv / length) if len(curv) else 0.0,
        "max_forward_curvature_bp": float(np.abs(curv).max()) if len(curv) else 0.0,
        "objective": fit_term
        + params.bid_ask_penalty * ba_term
        + params.smoothness * rough_term
        + params.long_end_flatness * flat_term,
        "objective_terms": {"fit": fit_term, "bid_ask": ba_term, "roughness": rough_term, "flatness": flat_term},
        "outer_iterations": outer,
        "converged": converged,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "grid_nodes": len(grid_dates),
        "grid_months": n_months,
    }

    warns: list[str] = []
    if not converged:
        warns.append("Оптимизатор не сошёлся за 30 итераций — проверьте котировки и параметры")
    if abs_e.max() > d_max + _TOL_BP:
        warns.append(f"Превышено предельное отклонение точки: {abs_e.max():.2f} бп > {d_max:.2f} бп")
    if abs_e.mean() > d_avg + _TOL_BP:
        warns.append(f"Превышено предельное среднее отклонение: {abs_e.mean():.2f} бп > {d_avg:.2f} бп")
    outside = [f.tenor for f in fitted if not f.in_spread]
    if outside:
        warns.append("Скорректированный мид вне бид-аск: " + ", ".join(outside))

    return CalibrationResult(as_of, eff_params, nodes, fitted, stats, warns)


def _solve_adjustments(
    b0: np.ndarray,
    p_mat: np.ndarray,
    half: np.ndarray,
    kappa: float,
    d_max: float,
    d_avg: float,
    start: np.ndarray,
) -> np.ndarray:
    """Choose mid adjustments e [bp] (n variables) for the linearised problem.

    min  1/n Σ e² + κ/n Σ ((|e|−h)_+)² + (b0 + e/BP)' P (b0 + e/BP)
    s.t. |e_i| ≤ D_max,  1/n Σ |e_i| ≤ D_avg     (|e| via auxiliary u ≥ ±e)
    Small convex problem: n adjustments + n auxiliaries.
    """
    n = len(b0)

    def fun(z: np.ndarray) -> tuple[float, np.ndarray]:
        e = z[:n]
        hi, lo = np.maximum(e - half, 0.0), np.maximum(-e - half, 0.0)
        b = b0 + e / BP
        pb = p_mat @ b
        val = (e @ e + kappa * (hi @ hi + lo @ lo)) / n + b @ pb
        grad = np.zeros(2 * n)
        grad[:n] = (2.0 * e + 2.0 * kappa * (hi - lo)) / n + 2.0 * pb / BP
        return float(val), grad

    eye = np.eye(n)
    a_ub = np.vstack(
        [np.hstack([-eye, eye]), np.hstack([eye, eye]), np.concatenate([np.zeros(n), -np.ones(n)])[None, :]]
    )
    b_ub = np.concatenate([np.zeros(2 * n), [n * d_avg]])
    e0 = np.clip(start, -d_max, d_max)
    z0 = np.concatenate([e0, np.abs(e0)])
    res = minimize(
        fun,
        z0,
        jac=True,
        method="SLSQP",
        bounds=[(-d_max, d_max)] * n + [(0.0, max(d_max, 0.0))] * n,
        constraints=[{"type": "ineq", "fun": lambda z: a_ub @ z + b_ub, "jac": lambda z: a_ub}],
        options={"maxiter": 300, "ftol": 1e-14},
    )
    return np.clip(res.x[:n], -d_max, d_max)


def _par_rates(
    as_of: date, grid_dates: list[date], monthly: dict[date, int], dfs: np.ndarray, conv: SwapConventions
) -> list[float | None]:
    """Par rate of a swap maturing at every node (fixed-leg payment dates are monthly nodes)."""
    dc = conv.day_count
    pos = {d: j for j, d in enumerate(grid_dates)}
    out: list[float | None] = [None]
    for j in range(1, len(grid_dates)):
        d = grid_dates[j]
        k = monthly.get(d)
        if k is None or k <= conv.single_payment_max_months:
            out.append(float((1.0 / dfs[j] - 1.0) / dc.year_fraction(as_of, d) * 100.0))
            continue
        pays = conv.payment_dates_for_months(as_of, k)
        prev = [as_of] + pays[:-1]
        ann = sum(dc.year_fraction(a, b) * dfs[pos[b]] for a, b in zip(prev, pays))
        out.append(float((1.0 - dfs[j]) / ann * 100.0))
    return out
