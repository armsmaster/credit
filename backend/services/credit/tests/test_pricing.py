import math
from datetime import date, timedelta

import numpy as np
import pytest

from credit_service.domain.bonds import AmortRow, BondKind, BondProfile, CouponRow, RateIndex
from credit_service.domain.calibration import BondFitInput, CreditParams, calibrate
from credit_service.domain.cashflows import project
from credit_service.domain.pricing import BondPricer, HazardGrid
from credit_service.domain.rates import RateCurve

D = date(2026, 11, 19)
S = D + timedelta(days=1)


def zero_bond(years: float, kind=BondKind.FIXED, **kw) -> BondProfile:
    mat = D + timedelta(days=round(years * 365))
    return BondProfile(secid=f"Z{years}", kind=kind, mat_date=mat, initial_face=1000.0, **kw)


def coupon_bond(years: float, rate: float, months: int = 6, **kw) -> BondProfile:
    mat = D + timedelta(days=round(years * 365))
    dates = []
    d = mat
    while d > D - timedelta(days=months * 31):
        dates.append(d)
        d = d - timedelta(days=round(months * 30.4))
    dates.sort()
    coupons = [CouponRow(start, end, None, None, rate) for start, end in zip(dates[:-1], dates[1:])]
    return BondProfile(secid=f"C{years}", kind=BondKind.FIXED, mat_date=mat, initial_face=1000.0, coupons=coupons, **kw)


def pricer(p: BondProfile, *, r=0.15, recovery=0.0, curves=None, face=1000.0, accrued=0.0, discount=None):
    disc = discount or RateCurve.flat("OIS", D, r)
    bond = project(p, curve_date=D, settle_date=S, face_settle=face, curves=curves or {})
    return BondPricer(bond, HazardGrid.monthly(D), disc, recovery, accrued)


def test_zero_coupon_without_recovery_is_risky_discounting():
    lam, r = 0.03, 0.15
    pr = pricer(zero_bond(3.0), r=r)
    tau = (pr.bond.maturity - S).days / 365.0
    expected = 1000.0 * math.exp(-(r + lam) * tau)
    assert pr.dirty(np.full(120, lam)) == pytest.approx(expected, rel=1e-10)


def test_recovery_leg_matches_closed_form():
    lam, r, R = 0.05, 0.12, 0.4
    pr = pricer(zero_bond(4.0), r=r, recovery=R)
    tau = (pr.bond.maturity - S).days / 365.0
    k = r + lam
    expected = 1000.0 * (math.exp(-k * tau) + R * lam / k * (1 - math.exp(-k * tau)))
    assert pr.dirty(np.full(120, lam)) == pytest.approx(expected, rel=2e-4)


def test_gradient_matches_finite_differences():
    pr = pricer(coupon_bond(5.0, 14.0), recovery=0.35)
    rng = np.random.default_rng(1)
    hz = 0.02 + 0.01 * rng.random(120)
    _, grad = pr.clean_pct_and_grad(hz)
    eps = 1e-7
    for k in (0, 7, 30, 59, 61, 119):
        up, dn = hz.copy(), hz.copy()
        up[k] += eps
        dn[k] -= eps
        fd = (pr.clean_pct(up) - pr.clean_pct(dn)) / (2 * eps)
        assert grad[k] == pytest.approx(fd, rel=1e-5, abs=1e-8)


def test_z_spread_of_risky_price_is_close_to_hazard_times_loss():
    lam, R = 0.02, 0.4
    pr = pricer(coupon_bond(3.0, 15.0), recovery=R)
    z = pr.z_spread(pr.clean_pct(np.full(120, lam)))
    assert z == pytest.approx(lam * (1 - R) * 1e4, rel=0.08)


def test_floating_coupon_projects_index_forward_plus_margin():
    p = coupon_bond(2.0, None, months=3)  # type: ignore[arg-type]
    p.kind, p.coupon_index, p.spread_pct = BondKind.FLOATING, RateIndex.KEYRATE, 1.25
    key = RateCurve.flat("KEY", D, 0.16)
    bond = project(p, curve_date=D, settle_date=S, face_settle=1000.0, curves={RateIndex.KEYRATE: key})
    flows = [f for f in bond.flows if f.kind == "coupon"]
    c, prev = flows[-1], flows[-2]
    days = (c.date - prev.date).days
    fwd = (math.exp(0.16 * days / 365) - 1) * 365 / days * 100  # simple ACT/365 rate of the period
    assert c.rate_pct == pytest.approx(fwd + 1.25, abs=1e-9)
    assert c.amount == pytest.approx(1000 * c.rate_pct / 100 * days / 365)
    assert c.projected


def test_linker_without_coupons_and_default_prices_at_par():
    ruonia = RateCurve.flat("RUONIA", D, 0.14)
    p = zero_bond(3.0, kind=BondKind.LINKER, notional_index=RateIndex.RUONIA)
    pr = pricer(p, discount=ruonia, curves={RateIndex.RUONIA: ruonia}, face=1074.94)
    assert pr.clean_pct(np.zeros(120)) == pytest.approx(100.0, abs=1e-9)
    final = pr.bond.flows[-1]
    tau = (pr.bond.maturity - S).days / 365.0
    assert final.amount == pytest.approx(1074.94 * math.exp(0.14 * tau), rel=1e-9)


def test_put_offer_cuts_cash_flows_at_offer_date():
    p = coupon_bond(6.0, 14.0)
    p.offer_date = D + timedelta(days=400)
    p.offer_price = 100.0
    bond = project(p, curve_date=D, settle_date=S, face_settle=1000.0, curves={})
    assert bond.maturity == p.offer_date
    assert bond.flows[-1].kind == "redemption" and bond.flows[-1].date == p.offer_date
    assert all(f.date <= p.offer_date for f in bond.flows)


def test_amortizations_sum_to_outstanding_notional():
    p = coupon_bond(3.0, 15.0, months=3)
    p.amortizations = [AmortRow(D + timedelta(days=d), 250.0, 25.0, False) for d in (180, 365, 545)]
    p.amortizations.append(AmortRow(p.mat_date, 250.0, 25.0, True))
    bond = project(p, curve_date=D, settle_date=S, face_settle=1000.0, curves={})
    redemptions = [f.amount for f in bond.flows if f.kind != "coupon"]
    assert sum(redemptions) == pytest.approx(1000.0)
    assert bond.notional_at(np.array([10.0, 200.0, 400.0]))[2] == pytest.approx(500.0)
    # coupons after an amortization are paid on the reduced notional
    late = [f for f in bond.flows if f.kind == "coupon" and (f.date - D).days > 560]
    assert late[0].amount < [f for f in bond.flows if f.kind == "coupon"][0].amount


def _true_hazard(t: np.ndarray) -> np.ndarray:
    return 0.01 + 0.025 * (1 - np.exp(-t / 3.0))


def test_calibration_recovers_known_curve_from_exact_prices():
    grid = HazardGrid.monthly(D)
    mid_t = 0.5 * (grid.knots[1:] + grid.knots[:-1])
    true = _true_hazard(mid_t)
    params = CreditParams(recovery_rate=0.4, smoothness=1.0)
    bonds = []
    for years, rate in [(0.8, 13.0), (1.5, 14.0), (2.2, 14.5), (3.0, 15.0), (4.1, 15.0), (5.5, 15.5), (7.0, 16.0)]:
        pr = pricer(coupon_bond(years, rate), recovery=params.recovery_rate)
        price = pr.clean_pct(true)
        bonds.append(BondFitInput(pr.bond.secid, pr.bond.secid, pr, {"dema": price, "bid": price - 0.05, "ask": price + 0.05}))
    out = calibrate(D, bonds, params)
    assert max(abs(f["error_bp"]) for f in out.fitted) < 1.0
    fitted = np.array([n["hazard"] for n in out.nodes[:-1]]) / 100.0
    region = mid_t < 7.0
    assert np.max(np.abs(fitted[region] - true[region])) < 0.004  # within 40 bp of the truth
    # beyond the last bond the curve is flat
    tail = fitted[85:]
    assert np.ptp(tail) < 1e-4


def test_secondary_observations_have_lower_priority():
    params = CreditParams(recovery_rate=0.4)
    pr = pricer(coupon_bond(3.0, 15.0), recovery=0.4)
    bonds = [BondFitInput("B", "B", pr, {"dema": 100.0, "bid": 99.9, "ask": 100.1, "last": 98.0, "vwap": 98.0})]
    out = calibrate(D, bonds, params)
    model = out.fitted[0]["model_price"]
    # weighted mean: (3·100 + 2·0.25·98) / 3.5 = 99.714
    assert model == pytest.approx(99.714, abs=0.01)
