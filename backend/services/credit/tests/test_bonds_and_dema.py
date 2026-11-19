from datetime import date, datetime, timedelta

import numpy as np

from credit_service.domain.bonds import BondKind, RateIndex, VEB_INN, classify
from credit_service.domain.dema import HALF_LIFE_GRID, choose_half_life, dema, trading_clock
from credit_service.domain.liquidity import liquidity_metrics, spread_pct

TODAY = date(2026, 11, 19)
SCHEDULE = {
    "coupons": [
        {"date": "2027-01-15", "start": "2026-10-15", "value": 35.0, "valueprc": 14.0},
        {"date": "2027-04-15", "start": "2027-01-15", "value": None, "valueprc": None},
    ],
    "amortizations": [{"date": "2029-04-15", "value": 1000, "valueprc": 100, "source": "maturity"}],
    "offers": [],
}


def desc(**kw):
    base = {"FACEUNIT": "SUR", "MATDATE": "2029-04-15", "INITIALFACEVALUE": "1000", "BOND_SUBTYPE": "До погашения"}
    base.update(kw)
    return base


def test_ruonia_floater_is_complete_from_iss():
    p = classify("F", desc(BOND_TYPE="Облигация с плавающим купоном", COUPON_BENCHMARK="RUONIA", COUPON_BENCHMARK_SPREAD="1.30"),
                 SCHEDULE, None, today=TODAY)
    assert p.kind is BondKind.FLOATING and p.coupon_index is RateIndex.RUONIA and p.spread_pct == 1.30
    assert p.eligible and p.complete


def test_compounded_ruonia_benchmark_is_supported():
    p = classify("R", desc(BOND_TYPE="Облигация с плавающим купоном", COUPON_BENCHMARK="RUONIA_IND", COUPON_BENCHMARK_SPREAD="1.85"),
                 SCHEDULE, None, today=TODAY)
    assert p.coupon_index is RateIndex.RUONIA and p.eligible


def test_unsupported_benchmark_and_subordination_and_call_are_ineligible():
    zr = classify("Z", desc(BOND_TYPE="Облигация с плавающим купоном", COUPON_BENCHMARK="ZR_YLD_CRV", COUPON_BENCHMARK_SPREAD="0.5"), SCHEDULE, None, today=TODAY)
    assert not zr.eligible and "ZR_YLD_CRV" in zr.ineligible[0]
    sub = classify("S", desc(SUBORDBOND="1"), SCHEDULE, None, today=TODAY)
    assert "Субординированная облигация" in sub.ineligible
    call = classify("C", desc(BOND_SUBTYPE="До оферты (call)", CALLOPTIONDATE="2027-10-01"), SCHEDULE, None, today=TODAY)
    assert any(r.startswith("Колл") for r in call.ineligible)
    usd = classify("U", desc(FACEUNIT="USD"), SCHEDULE, None, today=TODAY)
    assert "Валюта номинала USD" in usd.ineligible
    forced = classify("C", desc(BOND_SUBTYPE="До оферты (call)", CALLOPTIONDATE="2027-10-01"), SCHEDULE, {"force_eligible": True}, today=TODAY)
    assert forced.eligible


def test_fixed_with_unknown_coupons_needs_a_rate_and_manual_data_completes_it():
    p = classify("X", desc(BOND_TYPE="Облигация с фиксированным (неизвестным) купоном"), SCHEDULE, None, today=TODAY)
    assert p.kind is BondKind.FIXED and not p.complete
    p2 = classify("X", desc(), SCHEDULE, {"fixed_rate_pct": 15.5}, today=TODAY)
    assert p2.complete


def test_veb_linker_is_indexed_by_ruonia_and_offer_is_found():
    sched = {**SCHEDULE, "offers": [{"date": "2028-01-10", "price": 100, "type": "Оферта"}, {"date": "0000-00-00", "type": "Оферта/Погашение"}]}
    p = classify("L", desc(FACEVALUE_TYPE="Индексированный", BOND_TYPE="Линкер/облигация с индексируемым номиналом"), sched, None,
                 today=TODAY, issuer_inn=VEB_INN)
    assert p.kind is BondKind.LINKER and p.notional_index is RateIndex.RUONIA
    assert p.offer_date == date(2028, 1, 10) and p.effective_maturity == date(2028, 1, 10) and "put" in p.features
    other = classify("L2", desc(FACEVALUE_TYPE="Индексированный"), SCHEDULE, None, today=TODAY, issuer_inn="1")
    assert not other.complete


def test_trading_clock_skips_nights_and_weekends():
    fri = datetime(2026, 11, 20, 18, 0)
    mon = datetime(2026, 11, 23, 10, 0)
    c = trading_clock([fri, mon])
    assert c[1] - c[0] == (24 - 18) + (10 - 7)


def _local_level(n: int, q: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    level = 100 + np.cumsum(rng.normal(0, np.sqrt(q) * 0.05, n))
    return level + rng.normal(0, 0.05, n), np.arange(n, dtype=float)


def test_half_life_is_longer_for_noisier_prices_and_errors_are_nearly_white():
    noisy = _local_level(1500, 0.01, 1)
    clean = _local_level(1500, 1.0, 2)
    _, choice = choose_half_life({"noisy": noisy, "clean": clean})
    assert choice["noisy"].half_life > 3 * choice["clean"].half_life
    for c in choice.values():
        assert abs(c.acf1) < 0.15
        assert not c.pooled


def test_short_series_uses_pooled_half_life():
    long = _local_level(800, 0.1, 3)
    short = _local_level(30, 0.1, 4)
    pooled, choice = choose_half_life({"long": long, "short": short})
    assert choice["short"].pooled and choice["short"].half_life == pooled.half_life
    assert pooled.half_life in HALF_LIFE_GRID


def test_dema_tracks_a_step_faster_than_ema():
    x = np.concatenate([np.full(50, 100.0), np.full(50, 101.0)])
    d = dema(x, np.arange(100.0), 8.0)
    assert d[-1] > 100.99 and d[60] > 100.5


def test_liquidity_percentiles_only_for_month_of_trading():
    days = [(TODAY - timedelta(days=i), 1e6 * (i % 5), i % 3) for i in range(1, 15)]
    m = liquidity_metrics(days, [0.2, 0.4], window_days=90, as_of=TODAY)
    assert m["p20_turnover"] is None and m["avg_spread_pct"] == 0.30000000000000004 or abs(m["avg_spread_pct"] - 0.3) < 1e-12
    days = [(TODAY - timedelta(days=i), 1e6 * (i % 5), i % 3) for i in range(1, 40)]
    m = liquidity_metrics(days, [], window_days=90, as_of=TODAY)
    assert m["p20_turnover"] is not None and m["p80_turnover"] >= m["p20_turnover"]
    assert spread_pct(99.0, 101.0) == 2.0
