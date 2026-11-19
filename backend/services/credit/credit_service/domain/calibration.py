"""Bootstrapping a smooth credit curve from a cloud of bond prices.

Unknowns: forward hazard rates x_k [bp] on 120 monthly segments (10 years), flat beyond.

    minimise  J(x) = FIT + α·ROUGH + β·FLAT,     x ≥ 0

    FIT   = Σ_b Σ_o W_bo · e_bo²          e_bo = (P_b(x) − O_bo) / DV01_b      [bp of spread]
    ROUGH = 1/L · Σ_k (x_{k+1} − 2x_k + x_{k−1})²                            (curvature, months)
    FLAT  = 1/L_f · Σ_{k ≥ K_f} (x_{k+1} − x_k)²                             (flat long end)

* O_bo — observed clean prices of bond b: DEMA, BID, ASK (priority 1, weight 1), LAST and VWAP
  (priority 2, weight ``secondary_weight``), or a manual price (priority 1);
* W_bo = w_b · π_o normalised to sum to 1, so FIT is a weighted mean square error;
* price errors are converted to bp of spread with the bond's DV01 (price change per 1 bp of
  discount rate) — a 10-year bond and a 1-year bond are compared in the same units;
* α (``smoothness``) is the single knob for the smoothness of forward hazard rates;
* K_f — the month of the last effective maturity (``long_end_flat_from_months`` overrides it):
  beyond the last bond there is no information, the curve continues flat.

Solved by trust-region least squares (scipy, method 'trf') with bounds x ≥ 0 and the analytic
Jacobian of BondPricer; the start is the best flat curve. See docs/credit-math.md.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from .pricing import BondPricer, HazardGrid

BP = 1e-4
PRIMARY = ("dema", "bid", "ask", "manual")
SECONDARY = ("last", "vwap")
OBS_TITLES = {"dema": "DEMA", "bid": "BID", "ask": "ASK", "last": "LAST", "vwap": "VWAP", "manual": "Ручная"}


@dataclass(frozen=True)
class CreditParams:
    recovery_rate: float = 0.4
    smoothness: float = 100.0
    long_end_flatness: float = 100.0
    long_end_flat_from_months: int | None = None  # None: from the last effective maturity
    secondary_weight: float = 0.25  # weight of LAST and VWAP relative to DEMA / BID / ASK
    short_bond_years: float = 0.5  # bonds closer to maturity are suggested for exclusion

    def with_overrides(self, overrides: dict[str, Any] | None) -> "CreditParams":
        if not overrides:
            return self
        known = {k: v for k, v in overrides.items() if k in self.__dataclass_fields__}
        return replace(self, **known)

    def validate(self) -> None:
        if not 0 <= self.recovery_rate < 1:
            raise ValueError("Recovery rate — в диапазоне [0; 1)")
        if self.smoothness < 0 or self.long_end_flatness < 0:
            raise ValueError("Веса штрафов не могут быть отрицательными")
        if not 0 <= self.secondary_weight <= 1:
            raise ValueError("Вес LAST/VWAP — в диапазоне [0; 1]")
        if self.long_end_flat_from_months is not None and not 1 <= self.long_end_flat_from_months <= 120:
            raise ValueError("Начало плоского участка — от 1 до 120 месяцев")
        if self.short_bond_years < 0:
            raise ValueError("Порог короткой бумаги не может быть отрицательным")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BondFitInput:
    secid: str
    label: str
    pricer: BondPricer
    observations: dict[str, float]  # kind -> clean price, %
    weight: float = 1.0
    info: dict[str, Any] = field(default_factory=dict)  # passed through to the result


@dataclass
class CalibrationOutput:
    nodes: list[dict[str, Any]]
    fitted: list[dict[str, Any]]
    stats: dict[str, Any]
    warnings: list[str]
    params: dict[str, Any]


def _obs_weight(kind: str, params: CreditParams) -> float:
    return 1.0 if kind in PRIMARY else params.secondary_weight if kind in SECONDARY else 0.0


def calibrate(curve_date: date, bonds: list[BondFitInput], params: CreditParams) -> CalibrationOutput:
    started = time.perf_counter()
    params.validate()
    grid = HazardGrid.monthly(curve_date)
    K = grid.size
    warnings: list[str] = []

    # ---- residual layout: one row per (bond, observation)
    rows: list[tuple[int, str, float, float]] = []  # bond index, kind, observed price, weight
    for b, bond in enumerate(bonds):
        for kind, price in bond.observations.items():
            w = bond.weight * _obs_weight(kind, params)
            if price is not None and w > 0 and math.isfinite(price):
                rows.append((b, kind, float(price), w))
    if not rows:
        raise ValueError("Нет ни одной цены для калибровки: добавьте бумаги с котировками")
    used = sorted({r[0] for r in rows})
    for b, bond in enumerate(bonds):
        if b not in used:
            warnings.append(f"{bond.label}: нет котировок — бумага не участвует в расчёте")
    total_w = sum(r[3] for r in rows)
    row_bond = np.array([r[0] for r in rows])
    row_obs = np.array([r[2] for r in rows])
    row_sw = np.sqrt(np.array([r[3] for r in rows]) / total_w)

    # ---- penalties
    last_month = max(grid.month_index(bonds[b].pricer.bond.maturity) for b in used)
    flat_from = params.long_end_flat_from_months or last_month
    flat_from = min(max(flat_from, 1), K)
    d2 = np.diff(np.eye(K), n=2, axis=0)
    d1 = np.diff(np.eye(K), n=1, axis=0)[flat_from - 1:] if flat_from < K else np.zeros((0, K))
    pen = np.vstack([
        math.sqrt(params.smoothness / K) * d2,
        math.sqrt(params.long_end_flatness / max(len(d1), 1)) * d1,
    ])

    def model(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        prices = np.empty(len(bonds))
        grads = np.zeros((len(bonds), K))
        for b in used:
            prices[b], g = bonds[b].pricer.clean_pct_and_grad(x * BP)
            grads[b] = g * BP
        return prices, grads

    # ---- start: best flat hazard curve, then DV01 scales at that point
    def flat_res(z: np.ndarray) -> np.ndarray:
        x = np.full(K, z[0])
        prices = np.array([bonds[b].pricer.clean_pct(x * BP) if b in used else 0.0 for b in range(len(bonds))])
        return row_sw * (prices[row_bond] - row_obs)

    z0 = least_squares(flat_res, x0=[200.0], bounds=([0.0], [1e5]), method="trf").x[0]
    x0 = np.full(K, z0)
    dv01 = np.array([bonds[b].pricer.spread_duration(x0 * BP) if b in used else 1.0 for b in range(len(bonds))])
    dv01 = np.maximum(dv01, 1e-6)
    row_scale = row_sw / dv01[row_bond]

    def residuals(x: np.ndarray) -> np.ndarray:
        prices, _ = model(x)
        return np.concatenate([row_scale * (prices[row_bond] - row_obs), pen @ x])

    def jacobian(x: np.ndarray) -> np.ndarray:
        _, grads = model(x)
        return np.vstack([row_scale[:, None] * grads[row_bond], pen])

    sol = least_squares(residuals, x0, jac=jacobian, bounds=(np.zeros(K), np.full(K, 1e5)), method="trf",
                        x_scale=100.0, max_nfev=300, xtol=1e-10, ftol=1e-12, gtol=1e-10)
    x = sol.x
    if not sol.success:
        warnings.append(f"Оптимизатор остановился без сходимости: {sol.message}")
    if np.any(x < 0.5) and z0 < 1.0:
        warnings.append("Цены бумаг выше безрисковых: hazard rate упёрся в ноль — проверьте кривую дисконтирования")

    # ---- result
    prices, _ = model(x)
    R = params.recovery_rate
    hazard_dec = x * BP
    cum = grid.exposure(grid.knots) @ hazard_dec
    nodes = []
    for k, (d, t) in enumerate(zip(grid.knot_dates, grid.knots)):
        q = math.exp(-cum[k])
        avg = cum[k] / t if t > 0 else hazard_dec[0]
        nodes.append({
            "date": d.isoformat(),
            "t": round(float(t), 6),
            "months": k,
            "hazard": float(x[min(k, K - 1)]) / 100.0,  # forward hazard from this node, % p.a.
            "avg_hazard": float(avg) * 100.0,  # % p.a.
            "survival": q,
            "pd": 1.0 - q,
            "spread_bp": float(avg) * (1.0 - R) * 1e4,  # credit triangle: λ̄·(1−R)
        })

    fitted, all_err, all_w = [], [], []
    for b, bond in enumerate(bonds):
        pr = bond.pricer
        item: dict[str, Any] = {
            "secid": bond.secid,
            "label": bond.label,
            "weight": bond.weight,
            "maturity": pr.bond.maturity.isoformat(),
            "years": round((pr.bond.maturity - curve_date).days / 365.0, 4),
            "face": pr.face,
            "accrued": pr.accrued,
            "used": b in used,
            **bond.info,
        }
        obs_out = []
        if b in used:
            model_price = float(prices[b])
            item["model_price"] = model_price
            item["dv01"] = float(dv01[b])
            item["z_spread_model_bp"] = pr.z_spread(model_price)
            ws = 0.0
            target = 0.0
            for kind, price in bond.observations.items():
                w = bond.weight * _obs_weight(kind, params)
                if price is None or w <= 0:
                    obs_out.append({"kind": kind, "price": price, "error_bp": None, "weight": 0.0})
                    continue
                err = (model_price - price) / dv01[b]
                obs_out.append({"kind": kind, "price": price, "error_bp": float(err), "weight": w})
                all_err.append(err)
                all_w.append(w)
                ws += w
                target += w * price
            target /= ws
            item["target_price"] = target
            item["error_bp"] = float((model_price - target) / dv01[b])
            item["z_spread_market_bp"] = pr.z_spread(target)
        item["observations"] = obs_out
        fitted.append(item)

    err = np.array(all_err)
    w = np.array(all_w)
    stats = {
        "n_bonds": len(used),
        "n_observations": len(rows),
        "rmse_bp": float(math.sqrt(np.sum(w * err**2) / np.sum(w))),
        "mean_abs_bp": float(np.sum(w * np.abs(err)) / np.sum(w)),
        "max_abs_bp": float(np.max(np.abs(err))),
        "roughness": float(np.sum((d2 @ x) ** 2) / K),
        "flat_from_months": flat_from,
        "flat_start_hazard_bp": float(z0),
        "iterations": int(sol.nfev),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "status": "ok" if sol.success else "not_converged",
    }
    out_params = params.to_dict()
    out_params["long_end_flat_from_months_resolved"] = flat_from
    return CalibrationOutput(nodes, fitted, stats, warnings, out_params)
