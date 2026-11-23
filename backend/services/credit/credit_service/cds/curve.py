"""CDS curve of a reference entity from its bond credit curve (level 3 of the specification).

1. Model par spreads of standard SPFI contracts (trade date = curve date, standard maturities 6M–5Y,
   semi-annual roll) on the hazard curve of the bond credit curve.
2. Add the CDS–bond basis b(T) per tenor (manual, 0 by default).
3. Sequential bootstrap of a piecewise-constant hazard curve λ(t) on (T_{i−1}, T_i] from these par
   spreads with the valuation recovery R.
4. From that curve: par spread, QS (flat curve with R_q), PUF at the standard coupons, survival,
   clean RPV01 and the payment schedule of every standard contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

from .calendar import BusinessCalendar
from .isda import (
    Conventions,
    DiscountCurve,
    HazardCurve,
    Quote,
    BracketError,
    Schedule,
    build_schedule,
    cds_legs,
    price,
    price_legs,
    puf_to_qs_detail,
    standard_maturity,
    yf_act365f,
    _solve,
    TENOR_MONTHS,
)


@dataclass
class CurveInputs:
    valuation: date
    bond_hazard: HazardCurve
    discount: DiscountCurve
    recovery: float
    recovery_quote: float
    tenors: list[str]
    standard_coupon: float
    coupons: list[float]
    basis_bp: dict[str, float]
    conv: Conventions
    cal: BusinessCalendar
    warnings: list[str] = field(default_factory=list)


def schedule_rows(sched: Schedule, legs, coupon: float) -> list[dict[str, Any]]:
    """Payment schedule of a contract per unit notional with the legs' discount and survival factors."""
    by_period = {id(c["period"]): c for c in legs.coupons}
    rows = []
    for p in sched.periods:
        c = by_period.get(id(p))
        rows.append({
            "accrual_start": p.accrual_start.isoformat(), "accrual_end": p.accrual_end.isoformat(),
            "pay_date": p.pay_date.isoformat(), "days": (p.accrual_end - p.accrual_start).days + (1 if p is sched.periods[-1] else 0),
            "year_frac": p.year_frac, "coupon": coupon * p.year_frac,
            "observation": c["observation"].isoformat() if c else None,
            "df_pay": c["df_pay"] if c else None, "survival": c["q_obs"] if c else None,
            "pv_coupon": coupon * p.year_frac * c["df_pay"] * c["q_obs"] if c else None,
            "pv_aod": coupon * c["aod"] if c else None,
        })
    return rows


def contract_result(sched: Schedule, valuation: date, hc: HazardCurve, dc: DiscountCurve, conv: Conventions,
                    recovery: float, recovery_quote: float, standard_coupon: float, coupons: list[float]) -> dict[str, Any]:
    legs = cds_legs(sched, valuation, recovery, conv, dc, hc)
    base = price_legs(legs, standard_coupon)
    qs, lam_q = puf_to_qs_detail(sched, valuation, standard_coupon, base["puf"], conv, dc, recovery_quote)
    return {
        "maturity": sched.maturity.isoformat(),
        "effective_date": sched.effective_date.isoformat(),
        "first_period_start": sched.first_period_start.isoformat(),
        "upfront_settle_date": sched.upfront_settle_date.isoformat(),
        "rebate_start": sched.rebate_start.isoformat() if sched.rebate_start else None,
        "rebate_end": sched.rebate_end.isoformat() if sched.rebate_end else None,
        "rebate_year_frac": sched.rebate_year_frac,
        "protection_start": legs.protection_start.isoformat(),
        "protection": legs.protection,
        "rpv01_dirty": legs.rpv01_dirty,
        "rpv01_clean": base["rpv01_clean"],
        "df_settle": legs.df_settle,
        "par_spread": base["par_spread"],
        "qs": qs,
        "qs_flat_lambda": lam_q,
        "puf": {f"{round(c * 1e4)}": price_legs(legs, c)["puf"] for c in coupons},
        "survival": hc.survival(sched.maturity),
        "schedule": schedule_rows(sched, legs, standard_coupon),
    }


def build(inp: CurveInputs) -> dict[str, Any]:
    T, conv, cal, dc = inp.valuation, inp.conv, inp.cal, inp.discount
    eff = cal.add_business_days(T, conv.effective_lag_bd) if conv.effective_lag_bd else T
    warnings = list(inp.warnings)
    rows, quotes = [], []
    for tenor in inp.tenors:
        mat = standard_maturity(eff, tenor)
        sched = build_schedule(T, mat, conv, cal)
        par_bond = price(sched, T, inp.standard_coupon, inp.recovery, conv, dc, inp.bond_hazard)["par_spread"]
        basis = float(inp.basis_bp.get(tenor) or 0.0) / 1e4
        rows.append({"tenor": tenor, "maturity": mat, "schedule": sched, "par_bond": par_bond, "basis": basis})
        quotes.append(Quote(mat, "PAR", par_bond + basis))

    # bootstrap; a node that needs λ <= 0 (arbitrage or a too negative basis) is dropped and flagged
    times: list[float] = []
    lams: list[float] = []
    for row, q in zip(rows, quotes):
        t_i = yf_act365f(T, q.maturity)
        sched = row["schedule"]
        try:
            lam = _solve(lambda l: price(sched, T, q.value, inp.recovery, conv, dc, HazardCurve(T, times + [t_i], lams + [l]))["puf"])
        except BracketError:
            row["dropped"] = True
            warnings.append(f"{row['tenor']}: par spread {q.value * 1e4:.1f} б.п. требует λ ≤ 0 — узел исключён")
            continue
        times.append(t_i)
        lams.append(lam)
        row["lambda"] = lam
    if not lams:
        raise ValueError("Не удалось построить CDS-кривую: ни один узел не дал положительной λ")
    hc = HazardCurve(T, times, lams)

    table = []
    for row in rows:
        res = contract_result(row["schedule"], T, hc, dc, conv, inp.recovery, inp.recovery_quote, inp.standard_coupon, inp.coupons)
        table.append({
            "tenor": row["tenor"], "par_bond_bp": row["par_bond"] * 1e4, "basis_bp": row["basis"] * 1e4,
            "target_par_bp": (row["par_bond"] + row["basis"]) * 1e4, "lambda": row.get("lambda"),
            "dropped": bool(row.get("dropped")), **res,
            "par_spread_bp": res["par_spread"] * 1e4, "qs_bp": res["qs"] * 1e4,
        })

    horizon = max(TENOR_MONTHS[t] for t in inp.tenors) + 12
    monthly = []
    for k in range(horizon + 1):
        d = T + relativedelta(months=k)
        t = yf_act365f(T, d)
        monthly.append({"date": d.isoformat(), "months": k, "t": t, "survival": hc.survival_t(t), "hazard": hc.lam(t + 1e-9),
                        "bond_survival": inp.bond_hazard.survival_t(t), "bond_hazard": inp.bond_hazard.lam(t + 1e-9)})
    return {
        "valuation_date": T.isoformat(),
        "effective_date": eff.isoformat(),
        "recovery": inp.recovery,
        "recovery_quote": inp.recovery_quote,
        "standard_coupon": inp.standard_coupon,
        "coupons": inp.coupons,
        "hazard": [{"t": t, "date": (T + timedelta(days=round(t * 365))).isoformat(), "lambda": lam} for t, lam in zip(times, lams)],
        "table": table,
        "monthly": monthly,
        "warnings": warnings,
    }
