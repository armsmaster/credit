"""Open API payloads: model mids of swaps from a stored curve (current state and history)."""
from datetime import date, datetime, timezone

from curves_service.models import CurveDefinition, CurveHistory, CurveState
from curves_service.public_api import curve_out

DEFN = CurveDefinition(code="RUB-RUONIA-OIS", name="OIS RUONIA", currency_code="RUB", index_name="RUONIA", day_count="ACT/365F",
                       fixed_frequency_months=12, single_payment_max_months=12)
RESULT = {"fitted": [{"tenor": "1Y", "maturity": "2027-11-27", "model": 15.87, "mid": 15.9},
                     {"tenor": "2Y", "maturity": "2028-11-27", "model": 14.95, "mid": 14.9}]}
SAVED = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def test_current_curve():
    out = curve_out(DEFN, CurveState(as_of_date=date(2026, 11, 27), result=RESULT, saved_at=SAVED), None)
    assert out["kind"] == "current" and out["version"] is None and out["stale"] is False and out["published_at"] == SAVED
    assert out["quotes"] == [{"tenor": "1Y", "maturity": "2027-11-27", "model_mid_pct": 15.87},
                             {"tenor": "2Y", "maturity": "2028-11-27", "model_mid_pct": 14.95}]
    assert (out["curve"], out["currency"], out["day_count"], out["fixed_frequency_months"]) == ("RUB-RUONIA-OIS", "RUB", "ACT/365F", 12)


def test_history_for_a_later_date_is_marked_stale():
    snap = CurveHistory(as_of_date=date(2026, 11, 25), version=2, result=RESULT, created_at=SAVED)
    out = curve_out(DEFN, snap, date(2026, 11, 26))
    assert out["kind"] == "history" and out["version"] == 2 and out["stale"] is True and out["requested_date"] == date(2026, 11, 26)
    assert curve_out(DEFN, snap, date(2026, 11, 25))["stale"] is False
