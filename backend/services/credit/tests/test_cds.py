"""CDS engine against the specification's test cases and the reference implementation."""
import math
from datetime import date, timedelta

import pytest

from credit_service.cds.calendar import WEEKDAYS_ONLY, BusinessCalendar
from credit_service.cds.curve import CurveInputs, build
from credit_service.cds.isda import (
    SBER_OTC,
    SPFI,
    DiscountCurve,
    HazardCurve,
    Quote,
    bootstrap,
    build_schedule,
    cds_legs,
    price,
    puf_to_qs,
    qs_to_puf,
    standard_maturity,
    with_settings,
    yf_act_act_isda,
    yf_act365f,
)

from .cds_reference_values import REFERENCE

cal = WEEKDAYS_ONLY
T, MAT, N = date(2026, 10, 28), date(2027, 9, 20), 1_000_000


def test_moex_example_schedule_and_rebate():
    s = build_schedule(T, MAT, SPFI, cal)
    assert s.effective_date == date(2026, 10, 29) and s.first_period_start == date(2026, 9, 21)
    assert [round(N * 0.01 * p.year_frac, 2) for p in s.periods] == [2493.15, 2493.15, 2493.15, 2520.55]
    assert [p.pay_date for p in s.periods] == [date(2026, 12, 21), date(2027, 3, 22), date(2027, 6, 21), date(2027, 9, 20)]
    assert round(N * 0.01 * s.rebate_year_frac, 2) == 1041.10  # by the specification text (38 days)
    slide = build_schedule(T, MAT, with_settings(SPFI, rebate_mode="unadjusted_start"), cal)
    assert round(N * 0.01 * slide.rebate_year_frac, 2) == 1068.49  # the slide's 39 days


def test_puf_of_the_example_matches_reference():
    s = build_schedule(T, MAT, SPFI, cal)
    for r in (0.10, 0.14, 0.18):
        for R in (0.40, 0.25):
            puf = qs_to_puf(s, T, 0.01, 0.012, SPFI, DiscountCurve.flat(T, r), R)
            assert puf == pytest.approx(REFERENCE["puf_qs120"][f"{r}_{R}"], abs=1e-10)
            assert abs(puf * 100 - 0.16) <= 0.0081  # ≈ 0.16% for RUB rates 10–18%


def test_round_trip_qs_puf():
    dc = DiscountCurve.flat(T, 0.14)
    s5 = build_schedule(T, standard_maturity(date(2026, 10, 29), "5Y"), SPFI, cal)
    for qs in (0.006, 0.012, 0.035, 0.08):
        puf = qs_to_puf(s5, T, 0.01, qs, SPFI, dc)
        assert puf_to_qs(s5, T, 0.01, puf, SPFI, dc) == pytest.approx(qs, abs=1e-9)
        assert qs_to_puf(s5, T, qs, qs, SPFI, dc) == pytest.approx(0.0, abs=1e-10)


def _quotes():
    eff = date(2026, 10, 29)
    return [Quote(standard_maturity(eff, "1Y"), "PAR", 0.0100), Quote(standard_maturity(eff, "3Y"), "PAR", 0.0115),
            Quote(standard_maturity(eff, "5Y"), "PAR", 0.0130)]


def test_bootstrap_reprices_nodes_and_matches_reference():
    dc = DiscountCurve.flat(T, 0.14)
    hc = bootstrap(T, T, _quotes(), 0.40, SPFI, cal, dc)
    assert hc.lambdas == pytest.approx(REFERENCE["boot_par"], abs=1e-9)
    for q in _quotes():
        assert price(build_schedule(T, q.maturity, SPFI, cal), T, q.value, 0.40, SPFI, dc, hc)["puf"] == pytest.approx(0.0, abs=1e-10)
    puf_quotes = [Quote(q.maturity, "PUF", v, 0.01) for q, v in zip(_quotes(), REFERENCE["boot_puf_values"])]
    hc2 = bootstrap(T, T, puf_quotes, 0.35, SPFI, cal, dc)
    assert hc2.lambdas == pytest.approx(REFERENCE["boot_puf35"], abs=1e-8)


def test_sber_template_trade():
    ts = date(2026, 11, 23)
    s = build_schedule(ts, date(2027, 3, 20), SBER_OTC, cal)
    assert s.effective_date == ts and s.rebate_year_frac == 0.0
    assert [round(1e8 * 0.0065 * p.year_frac, 2) for p in s.periods] == [49863.01, 160273.97]
    pr = price(s, ts, 0.0065, 0.40, SBER_OTC, DiscountCurve.flat(ts, 0.14), HazardCurve(ts, [100.0], [0.012 / 0.6]))
    for k, v in REFERENCE["sber"].items():
        assert pr[k] == pytest.approx(v, abs=1e-12)
    assert round(pr["par_spread"] * 1e4, 1) == 120.8 and round(-pr["pv_dirty"] * 1e8) == -173151


def test_curve_from_bond_hazard_reproduces_reference_table():
    """Level 3 with zero basis and the bond curve equal to the reference bootstrap: the same CDS table."""
    dc = DiscountCurve.flat(T, 0.14)
    hc = bootstrap(T, T, _quotes(), 0.40, SPFI, cal, dc)
    out = build(CurveInputs(T, hc, dc, 0.40, 0.40, ["6M", "1Y", "2Y", "3Y", "4Y", "5Y"], 0.01, [0.01, 0.05], {}, SPFI, cal))
    for row, ref in zip(out["table"], REFERENCE["table"]):
        assert row["maturity"] == ref["maturity"]
        assert row["par_spread_bp"] == pytest.approx(ref["par_spread_bp"], abs=1e-4)
        assert row["puf"]["100"] * 100 == pytest.approx(ref["puf_100bp_pct"], abs=1e-6)
        assert row["puf"]["500"] * 100 == pytest.approx(ref["puf_500bp_pct"], abs=1e-6)
        assert row["qs_bp"] == pytest.approx(ref["qs_bp"], abs=1e-4)
        assert row["survival"] == pytest.approx(ref["survival"], abs=1e-6)
        assert row["rpv01_clean"] == pytest.approx(ref["rpv01_clean"], abs=1e-6)
    assert out["table"][1]["schedule"][0]["accrual_start"] == "2026-09-21"


def test_basis_shifts_par_spreads_and_negative_lambda_node_is_dropped():
    dc = DiscountCurve.flat(T, 0.14)
    bond = HazardCurve.flat(T, 0.02)
    tenors = ["1Y", "3Y", "5Y"]
    base = build(CurveInputs(T, bond, dc, 0.4, 0.4, tenors, 0.01, [0.01, 0.05], {}, SPFI, cal))
    shifted = build(CurveInputs(T, bond, dc, 0.4, 0.4, tenors, 0.01, [0.01, 0.05], {"3Y": -10.0}, SPFI, cal))
    assert shifted["table"][1]["par_spread_bp"] == pytest.approx(base["table"][1]["par_spread_bp"] - 10.0, abs=1e-6)
    assert shifted["table"][0]["par_spread_bp"] == pytest.approx(base["table"][0]["par_spread_bp"], abs=1e-6)
    broken = build(CurveInputs(T, bond, dc, 0.4, 0.4, tenors, 0.01, [0.01], {"3Y": -500.0}, SPFI, cal))
    assert broken["table"][1]["dropped"] and any("3Y" in w for w in broken["warnings"])


def test_calendar_with_base_and_holidays():
    moscow = BusinessCalendar("MOSCOW", {date(2026, 11, 4): False, date(2026, 11, 1): True})
    spfi = BusinessCalendar("SPFI", {date(2026, 11, 5): False}, base=moscow)
    assert spfi.add_business_days(date(2026, 11, 3), 1) == date(2026, 11, 6)  # 4th — Moscow holiday, 5th — no SPFI session
    assert moscow.add_business_days(date(2026, 10, 30), 1) == date(2026, 11, 1)  # working Sunday
    assert spfi.following(date(2026, 11, 7)) == date(2026, 11, 9)


def test_analytic_integrals_against_numeric():
    tn = date(2026, 10, 28)
    dcn = DiscountCurve(tn, [date(2027, 1, 28), date(2027, 10, 28), date(2029, 10, 28), date(2031, 10, 28), date(2036, 10, 28)],
                        [math.exp(-0.16 * 0.25), math.exp(-0.15), math.exp(-0.42), math.exp(-0.675), math.exp(-1.3)])
    hcn = HazardCurve.from_dates(tn, [date(2027, 12, 20), date(2029, 12, 20), date(2031, 12, 20)], [0.016, 0.022, 0.03])
    sn = build_schedule(tn, date(2031, 12, 20), SPFI, cal)
    legs = cds_legs(sn, tn, 0.4, SPFI, dcn, hcn)
    h = 1 / 365 / 4  # coarser than the reference (1/20 day) to keep the test fast; midpoint rule error ~h²

    def integrate(ta, tb, weight):
        acc, t = 0.0, ta
        while t < tb - 1e-12:
            b = min(t + h, tb)
            m = 0.5 * (t + b)
            acc += weight(m) * dcn.df_t(m) * (hcn.survival_t(t) - hcn.survival_t(b))
            t = b
        return acc

    assert legs.protection == pytest.approx(0.6 * integrate(0.0, yf_act365f(tn, sn.maturity), lambda m: 1.0), abs=1e-8)
    rpv = 0.0
    for p in sn.periods:
        e_excl = p.accrual_end + timedelta(days=1) if p is sn.periods[-1] else p.accrual_end
        obs = e_excl - timedelta(days=1)
        rpv += p.year_frac * dcn.df(p.pay_date) * hcn.survival(obs)
        ts = yf_act365f(tn, p.accrual_start)

        def alpha(m, p=p):
            d = tn + timedelta(days=int(m * 365))
            frac = m * 365 - int(m * 365)
            return yf_act_act_isda(p.accrual_start, d) + frac / (366 if d.year % 4 == 0 else 365)

        rpv += integrate(max(ts, 0.0), yf_act365f(tn, obs), alpha)
    assert legs.rpv01_dirty == pytest.approx(rpv, abs=1e-7)
