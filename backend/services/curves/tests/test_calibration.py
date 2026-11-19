from datetime import date

import numpy as np
import pytest

from curves_service.domain.calibration import CalibrationParams, calibrate
from curves_service.domain.conventions import DayCount, SwapConventions, Tenor
from curves_service.domain.curve import Compounding, DiscountCurve, df_from_rate, rate_from_df
from curves_service.domain.quotes import InputMode, QuoteError, parse_quotes

AS_OF = date(2026, 11, 18)
CONV = SwapConventions()

# Illustrative (synthetic) quotes: easing cycle priced in, noisy mids.
RAW = [
    {"tenor": "1W", "bid": 20.90, "ask": 21.10},
    {"tenor": "1M", "bid": 20.70, "ask": 20.95},
    {"tenor": "2M", "bid": 20.40, "ask": 20.70},
    {"tenor": "3M", "bid": 20.05, "ask": 20.40},
    {"tenor": "6M", "bid": 19.20, "ask": 19.60},
    {"tenor": "9M", "bid": 18.45, "ask": 18.85},
    {"tenor": "1Y", "bid": 17.70, "ask": 18.10},
    {"tenor": "18M", "bid": 16.55, "ask": 17.05},
    {"tenor": "2Y", "bid": 15.95, "ask": 16.45},
    {"tenor": "3Y", "bid": 15.00, "ask": 15.60},
    {"tenor": "4Y", "bid": 14.70, "ask": 15.30},
    {"tenor": "5Y", "bid": 14.25, "ask": 14.85},
    {"tenor": "7Y", "bid": 14.05, "ask": 14.75},
    {"tenor": "10Y", "bid": 13.80, "ask": 14.60},
]


def quotes():
    return parse_quotes(RAW, InputMode.BID_ASK)


def test_tenor_parsing():
    assert Tenor.parse("3m").months == 3
    assert Tenor.parse("2Y").months == 24
    assert Tenor.parse("ON").maturity(AS_OF) == date(2026, 11, 19)
    assert Tenor.parse("1M").maturity(date(2026, 1, 31)) == date(2026, 2, 28)
    with pytest.raises(ValueError):
        Tenor.parse("3X")


def test_mid_spread_mode_equivalent():
    a = parse_quotes([{"tenor": "1Y", "bid": 17.7, "ask": 18.1}], InputMode.BID_ASK)
    b = parse_quotes([{"tenor": "1Y", "mid": 17.9, "spread": 0.4}], InputMode.MID_SPREAD)
    assert a[0].bid == pytest.approx(b[0].bid) and a[0].ask == pytest.approx(b[0].ask)


def test_quote_validation():
    with pytest.raises(QuoteError):
        parse_quotes([{"tenor": "1Y", "bid": 18.1, "ask": 17.7}], InputMode.BID_ASK)
    with pytest.raises(QuoteError):
        parse_quotes([{"tenor": "1Y", "bid": 1, "ask": 2}, {"tenor": "12M", "bid": 1, "ask": 2}], InputMode.BID_ASK)


def test_rate_conversions_roundtrip():
    for comp in Compounding:
        df = df_from_rate(0.15, 2.5, comp)
        assert rate_from_df(df, 2.5, comp) == pytest.approx(0.15)


def test_exact_fit_when_no_tolerance():
    p = CalibrationParams(max_avg_deviation_bp=0.0, max_point_deviation_bp=0.0, smoothness=1.0)
    res = calibrate(AS_OF, quotes(), CONV, p)
    assert res.stats["max_abs_deviation_bp"] < 0.1
    assert res.nodes[-1].months == 120


def test_constraints_respected():
    p = CalibrationParams(max_avg_deviation_bp=3.0, max_point_deviation_bp=8.0, smoothness=1000.0)
    res = calibrate(AS_OF, quotes(), CONV, p)
    assert res.stats["max_abs_deviation_bp"] <= 8.0 + 0.05
    assert res.stats["mean_abs_deviation_bp"] <= 3.0 + 0.05


def test_smoothness_knob_is_monotone():
    rough = []
    for lam in (0.01, 1.0, 100.0):
        res = calibrate(AS_OF, quotes(), CONV, CalibrationParams(smoothness=lam))
        rough.append(res.stats["roughness"])
    assert rough[0] >= rough[1] >= rough[2]


def test_long_end_is_flat():
    res = calibrate(AS_OF, quotes(), CONV, CalibrationParams(long_end_flatness=1000.0))
    fwd = np.array([n.forward for n in res.nodes[1:]])
    tail = fwd[84:]  # beyond 7Y
    assert np.abs(np.diff(tail)).max() < 1.0 / 100  # < 1 bp per month


def test_par_rate_of_fitted_curve_matches_model_quote():
    res = calibrate(AS_OF, quotes(), CONV, CalibrationParams())
    by_months = {n.months: n for n in res.nodes}
    for f in res.fitted:
        months = Tenor.parse(f.tenor).months
        if months:
            assert by_months[months].par == pytest.approx(f.model, abs=1e-6)


def test_discount_curve_interpolation():
    res = calibrate(AS_OF, quotes(), CONV, CalibrationParams())
    curve = res.curve(CONV)
    n6 = res.nodes[6]
    assert curve.df(n6.date) == pytest.approx(n6.df)
    # log-linear: DF in the middle of a month is the geometric mean of neighbours (equal halves)
    d = date(2027, 1, 3)
    t = curve.t(d)
    assert 0 < curve.df(d) < 1
    assert curve.zero_rate(d, Compounding.CONTINUOUS) == pytest.approx(-np.log(curve.df(d)) / t)
    # flat extrapolation beyond the last node
    far = date(2040, 1, 1)
    f1 = curve.instantaneous_forward(date(2036, 11, 30))
    f2 = curve.instantaneous_forward(far)
    assert f1 == pytest.approx(f2)


def test_discount_curve_roundtrip_from_nodes():
    res = calibrate(AS_OF, quotes(), CONV, CalibrationParams())
    c = DiscountCurve.from_nodes(AS_OF, DayCount.ACT_365F, [n.date for n in res.nodes], [n.df for n in res.nodes])
    assert c.df(res.nodes[24].date) == pytest.approx(res.nodes[24].df)


def test_single_quote_gives_flat_curve():
    q = parse_quotes([{"tenor": "1Y", "bid": 15.0, "ask": 15.2}], InputMode.BID_ASK)
    res = calibrate(AS_OF, q, CONV, CalibrationParams())
    fwd = [n.forward for n in res.nodes[1:]]
    assert max(fwd) - min(fwd) < 1e-3
    assert res.fitted[0].deviation_bp == pytest.approx(0.0, abs=0.01)


def test_long_curve_is_fast_and_converges():
    raw = RAW + [{"tenor": "15Y", "bid": 13.6, "ask": 14.6}, {"tenor": "20Y", "bid": 13.5, "ask": 14.5},
                 {"tenor": "30Y", "bid": 13.4, "ask": 14.6}]
    res = calibrate(AS_OF, parse_quotes(raw, InputMode.BID_ASK), CONV, CalibrationParams(smoothness=10))
    assert res.stats["converged"]
    assert res.nodes[-1].months == 360
    assert res.stats["elapsed_ms"] < 5000


def test_short_end_relief_reduces_short_deviations():
    strict = calibrate(AS_OF, quotes(), CONV, CalibrationParams(smoothness=10, short_end_weight=1.0))
    relief = calibrate(AS_OF, quotes(), CONV, CalibrationParams(smoothness=10, short_end_weight=0.1))
    assert relief.stats["max_abs_deviation_bp"] < strict.stats["max_abs_deviation_bp"]


def test_bid_ask_penalty_keeps_fit_inside_spread():
    res = calibrate(AS_OF, quotes(), CONV, CalibrationParams(smoothness=100, bid_ask_penalty=1000,
                                                             max_avg_deviation_bp=50, max_point_deviation_bp=50))
    assert res.stats["outside_spread"] == 0


def test_par_curve_is_linear_between_quoted_tenors():
    res = calibrate(AS_OF, quotes(), CONV, CalibrationParams())
    fit = {f.tenor: f for f in res.fitted}
    a, b = fit["1Y"], fit["2Y"]
    for n in res.nodes:
        if n.months is not None and 12 < n.months < 18:  # between 1Y and 18M quotes
            c = fit["18M"]
            expected = a.model + (c.model - a.model) * (n.t - a.t) / (c.t - a.t)
            assert n.par == pytest.approx(expected, abs=1e-9)
    assert b.model == pytest.approx(next(n.par for n in res.nodes if n.months == 24), abs=1e-9)


def test_with_linear_par_fixes_curves_saved_with_old_par():
    from curves_service.application import result_to_dict, with_linear_par

    stored = result_to_dict(calibrate(AS_OF, quotes(), CONV, CalibrationParams()), CONV)
    for n in stored["nodes"]:  # simulate the old, DF-derived par values
        n["par"] = None if n["par"] is None else n["par"] + 0.123
    fixed = with_linear_par(stored)
    fresh = result_to_dict(calibrate(AS_OF, quotes(), CONV, CalibrationParams()), CONV)
    assert [n["par"] for n in fixed["nodes"]] == pytest.approx([n["par"] for n in fresh["nodes"]], abs=1e-12)
    assert stored["nodes"][5]["par"] != fixed["nodes"][5]["par"]  # input not mutated
