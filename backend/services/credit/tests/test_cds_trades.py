"""CDS trades without a database: schedule generator, curve roll-forward, trade valuation, hand-edited schedules."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from cds_common.errors import AppError
from credit_service.cds.calendar import WEEKDAYS_ONLY as cal
from credit_service.cds.isda import SBER_OTC, SPFI, DiscountCurve, HazardCurve, build_schedule, price
from credit_service.cds.schedule import ScheduleParams, generate, rows_of
from credit_service.cds.trades import TradeApp, rebase, value_on

T, E, M = date(2026, 10, 28), date(2026, 10, 29), date(2031, 12, 20)
CFG = {"protection_from_effective": False}


def _key(s):
    return [(p.accrual_start, p.accrual_end, p.pay_date, round(p.year_frac, 12)) for p in s.periods]


def test_standard_dates_reproduce_spfi_and_sber_schedules():
    spfi = generate(T, E, M, True, ScheduleParams.from_dict({}), cal, settle=E)
    ref = build_schedule(T, M, SPFI, cal)
    assert _key(spfi) == _key(ref) and spfi.rebate_year_frac == pytest.approx(ref.rebate_year_frac, abs=1e-15)
    assert spfi.first_period_start == date(2026, 9, 21) and spfi.rebate_end == E
    sber = generate(T, T, date(2029, 3, 15), False, ScheduleParams.from_dict({}), cal, settle=T)
    assert _key(sber) == _key(build_schedule(T, date(2029, 3, 15), SBER_OTC, cal)) and sber.rebate_year_frac == 0.0


def test_standard_mode_ignores_custom_parameters():
    p = ScheduleParams.from_dict({"mode": "standard", "frequency_months": 6, "bdc": "preceding", "adjust_accrual": False})
    assert (p.frequency_months, p.bdc, p.adjust_accrual) == (3, "following", True)


def test_custom_generator_stubs_and_rolls():
    m = date(2028, 3, 15)
    short = generate(T, E, m, False, ScheduleParams.from_dict({"mode": "custom", "frequency_months": 6}), cal)
    assert [p.accrual_end for p in short.periods] == [date(2027, 3, 15), date(2027, 9, 15), m]
    long_ = generate(T, E, m, False, ScheduleParams.from_dict({"mode": "custom", "frequency_months": 6, "stub": "long"}), cal)
    assert [p.accrual_end for p in long_.periods] == [date(2027, 9, 15), m]
    fwd = generate(T, E, m, False, ScheduleParams.from_dict({"mode": "custom", "frequency_months": 3, "direction": "forward", "stub": "long"}), cal)
    assert fwd.periods[0].accrual_end == date(2027, 1, 29) and fwd.periods[-1].accrual_start == date(2027, 10, 29)
    mf = generate(T, E, date(2027, 6, 30), False, ScheduleParams.from_dict({"mode": "custom", "frequency_months": 1, "roll_day": 31,
                                                                             "bdc": "modified_following"}), cal)
    assert date(2027, 1, 29) in [p.accrual_end for p in mf.periods]  # 31.01.2027 is a Sunday, MF stays in January
    reb = generate(T, E, m, True, ScheduleParams.from_dict({"mode": "custom", "frequency_months": 6}), cal)
    assert reb.first_period_start == date(2026, 9, 15) and reb.rebate_start == date(2026, 9, 15) and reb.rebate_end == E


def test_rebase_is_exact_roll_forward():
    c, d = date(2026, 11, 20), date(2026, 11, 25)
    hc = HazardCurve(c, [0.5, 1.5, 5.0], [0.02, 0.03, 0.04])
    dc = DiscountCurve.flat(c, 0.15)
    hc2, dc2 = rebase(hc, dc, d)
    for x in (date(2027, 1, 10), date(2028, 6, 1), date(2032, 1, 1)):
        assert hc2.survival(x) == pytest.approx(hc.survival(x) / hc.survival(d), rel=1e-12)
        assert dc2.df(x) == pytest.approx(dc.df(x) / dc.df(d), rel=1e-12)


def _version(side="buyer", notional=1e8, coupon_bp=100.0, rebate=True, maturity=M, effective=E, params=None):
    p = ScheduleParams.from_dict(params or {})
    sched = generate(T, effective, maturity, rebate, p, cal, settle=effective)
    return SimpleNamespace(
        side=side, notional=notional, coupon_bp=coupon_bp, trade_date=T, effective_date=effective, maturity=maturity, rebate=rebate,
        schedule_params=p.to_dict(),
        schedule={"rows": rows_of(sched, notional, coupon_bp / 1e4, p.last_period_inclusive), "settle_date": effective.isoformat(),
                  "rebate_start": sched.rebate_start.isoformat() if sched.rebate_start else None,
                  "rebate_end": sched.rebate_end.isoformat() if sched.rebate_end else None, "rebate_year_frac": sched.rebate_year_frac})


def _snap(c=T, lambdas=(0.03, 0.035, 0.04), rate=0.15):
    dc = DiscountCurve.flat(c, rate)
    return SimpleNamespace(as_of_date=c, result={"recovery": 0.4, "hazard": [{"t": t, "lambda": lam} for t, lam in zip((1.0, 3.0, 5.2), lambdas)]},
                           inputs={"discount": {"code": "RUB", "as_of_date": c.isoformat(), "nodes": [{"date": d.isoformat(), "df": f} for d, f in zip(dc.dates, dc.dfs)]}})


def test_value_on_trade_date_matches_the_engine():
    v, snap = _version(), _snap()
    out = value_on(v, T, snap, CFG)
    hc = HazardCurve(T, [1.0, 3.0, 5.2], [0.03, 0.035, 0.04])
    dc = DiscountCurve(T, [date.fromisoformat(n["date"]) for n in snap.inputs["discount"]["nodes"]],
                       [n["df"] for n in snap.inputs["discount"]["nodes"]])
    ref = price(build_schedule(T, M, SPFI, cal), T, 0.01, 0.4, SPFI, dc, hc)
    assert out["npv"] == pytest.approx(1e8 * ref["pv_dirty"], rel=1e-12)
    assert out["par_spread_bp"] == pytest.approx(ref["par_spread"] * 1e4, rel=1e-12)
    assert out["accrued"] == pytest.approx(-1e8 * 0.01 * ref["accrued_frac"] * ref["df_settle"], rel=1e-12)  # the rebate
    assert out["clean"] == pytest.approx(out["npv"] - out["accrued"], abs=1e-6)
    seller = value_on(_version(side="seller"), T, snap, CFG)
    assert seller["npv"] == pytest.approx(-out["npv"]) and seller["cs01"] == pytest.approx(-out["cs01"]) and out["cs01"] > 0


def test_value_later_uses_rolled_curve_and_accrues():
    v, snap = _version(), _snap()
    d = date(2026, 12, 21)  # coupon date: the new period has just started
    out = value_on(v, d, snap, CFG)
    assert out["details"]["stale_days"] == (d - T).days and out["details"]["curve_date"] == T.isoformat()
    assert abs(out["accrued"]) < 1e8 * 0.01 * 2 / 365  # one day of accrual at most
    mid = value_on(v, date(2027, 2, 1), snap, CFG)
    assert mid["accrued"] < 0 and mid["details"]["next_payment"]["date"] == "2027-03-22"
    assert mid["details"]["next_payment"]["amount"] < 0  # the buyer pays the coupon


def test_hand_edited_rows():
    params = ScheduleParams.from_dict({})
    gen = generate(T, E, date(2027, 9, 20), True, params, cal, settle=E)
    generated = rows_of(gen, 1e8, 0.01)
    terms = {"_params": params, "notional": 1e8, "coupon_bp": 100.0, "maturity": date(2027, 9, 20)}
    rows = [dict(r) for r in generated]
    rows[1]["amount"], rows[1]["manual"] = 250_000.0, True
    out, edited = TradeApp._normalise_rows(None, rows, terms, generated)
    assert edited and out[1]["amount"] == 250_000.0 and out[1]["year_frac"] == pytest.approx(0.25)
    assert out[0]["amount"] == pytest.approx(generated[0]["amount"])
    moved = [dict(r) for r in generated]
    moved[0]["accrual_end"] = moved[1]["accrual_start"] = "2026-12-22"
    out2, edited2 = TradeApp._normalise_rows(None, moved, terms, generated)
    assert edited2 and out2[0]["days"] == (date(2026, 12, 22) - date(2026, 9, 21)).days and out2[0]["amount"] > generated[0]["amount"]
    gap = [dict(r) for r in generated]
    gap[1]["accrual_start"] = "2026-12-25"
    with pytest.raises(AppError):
        TradeApp._normalise_rows(None, gap, terms, generated)
    same, edited3 = TradeApp._normalise_rows(None, [dict(r) for r in generated], terms, generated)
    assert not edited3 and [r["amount"] for r in same] == pytest.approx([r["amount"] for r in generated])
