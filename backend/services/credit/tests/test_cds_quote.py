"""Calculator QS ↔ upfront (PUF) conversion (CdsApp._convert / _quote_source), without a database."""
from datetime import date

import pytest

from cds_common.errors import AppError
from credit_service.cds.application import CdsApp
from credit_service.cds.calendar import WEEKDAYS_ONLY
from credit_service.cds.curve import contract_result
from credit_service.cds.isda import SPFI, DiscountCurve, HazardCurve, build_schedule, standard_maturity

from .cds_reference_values import REFERENCE

T = date(2026, 10, 28)


def _ctx(maturity: date, coupon: float, rate: float = 0.14, rq: float = 0.40) -> dict:
    return {"sched": build_schedule(T, maturity, SPFI, WEEKDAYS_ONLY), "valuation": T, "coupon": coupon, "conv": SPFI,
            "dc": DiscountCurve.flat(T, rate), "res": {"recovery_quote": rq}}


def test_qs_to_puf_matches_the_specification_example():
    for r in (0.10, 0.14, 0.18):
        for rq in (0.40, 0.25):
            qs, puf, lam = CdsApp._convert(_ctx(date(2027, 9, 20), 0.01, r, rq), qs=0.012)
            assert qs == 0.012 and lam > 0
            assert puf == pytest.approx(REFERENCE["puf_qs120"][f"{r}_{rq}"], abs=1e-10)


def test_round_trip_both_directions():
    ctx = _ctx(standard_maturity(date(2026, 10, 29), "5Y"), 0.05)
    for qs in (0.006, 0.02, 0.05, 0.12):
        _, puf, lam = CdsApp._convert(ctx, qs=qs)
        qs_back, puf_back, lam_back = CdsApp._convert(ctx, puf=puf)
        assert qs_back == pytest.approx(qs, abs=1e-9) and puf_back == puf and lam_back == pytest.approx(lam, rel=1e-7)
    assert CdsApp._convert(ctx, qs=0.05)[1] == pytest.approx(0.0, abs=1e-10)  # QS = coupon → no upfront
    assert CdsApp._convert(ctx, qs=0.02)[1] < 0 < CdsApp._convert(ctx, qs=0.08)[1]  # buyer pays when QS > coupon


def test_model_puf_converts_to_model_qs():
    ctx = _ctx(standard_maturity(date(2026, 10, 29), "3Y"), 0.01)
    hc = HazardCurve(T, [0.5, 1.5, 3.2], [0.02, 0.035, 0.05])
    out = contract_result(ctx["sched"], T, hc, ctx["dc"], SPFI, 0.35, 0.40, 0.01, [0.01])
    qs, _, _ = CdsApp._convert(ctx, puf=out["puf"]["100"])
    assert qs == pytest.approx(out["qs"], abs=1e-10)


def test_out_of_range_quote_is_a_clear_error():
    ctx = _ctx(standard_maturity(date(2026, 10, 29), "5Y"), 0.01)
    with pytest.raises(AppError) as exc:
        CdsApp._convert(ctx, puf=-0.5)  # the buyer cannot receive 50% upfront on a 100 bp coupon
    assert exc.value.code == "invalid_quote"
    with pytest.raises(AppError):
        CdsApp._convert(ctx)


def test_quote_source():
    src = CdsApp._quote_source
    assert src({"qs_bp": 120, "upfront_pct": 0.5, "quote_input": "qs"}) == "qs"
    assert src({"qs_bp": 120, "upfront_pct": 0.5, "quote_input": "puf"}) == "puf"
    assert src({"qs_bp": None, "upfront_pct": 0.5, "quote_input": "qs"}) == "puf"  # the entered one is empty
    assert src({"qs_bp": 120}) == "qs" and src({"upfront_pct": 0.5}) == "puf" and src({}) is None
