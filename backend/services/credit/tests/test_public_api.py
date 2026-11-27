"""Open API payloads: model CDS quotes from a stored CDS curve (current state and history)."""
from datetime import date, datetime, timezone

import pytest

from credit_service.models import CdsEntity, CdsHistory, CdsState
from credit_service.public_api import curve_out

ENTITY = CdsEntity(code="VEB", name="ВЭБ.РФ", standard_coupon_bp=100)
RESULT = {"recovery": 0.4, "recovery_quote": 0.4, "effective_date": "2026-11-20",
          "table": [{"tenor": "1Y", "maturity": "2027-12-20", "par_spread_bp": 190.45, "qs_bp": 190.51, "puf": {"100": 0.00891, "500": -0.03048},
                     "survival": 0.9665, "dropped": False},
                    {"tenor": "5Y", "maturity": "2031-12-20", "par_spread_bp": 232.0, "qs_bp": 236.2, "puf": {"100": 0.0453, "500": -0.0919},
                     "survival": 0.858, "dropped": True}]}
AT = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)


def test_current_cds_curve():
    snap = CdsState(as_of_date=date(2026, 11, 19), result=RESULT, inputs={"conventions": {"name": "SPFI_STD"}}, calculated_at=AT)
    out = curve_out(ENTITY, snap, "VEB", None)
    assert out["kind"] == "current" and out["conventions"] == "SPFI_STD" and out["credit_curve"] == "VEB" and out["published_at"] == AT
    q1, q5 = out["quotes"]
    assert q1["par_spread_bp"] == 190.45 and q1["puf_pct"] == pytest.approx({"100": 0.891, "500": -3.048}) and q1["excluded"] is False
    assert q5["excluded"] is True


def test_history_for_a_later_date_is_marked_stale():
    snap = CdsHistory(as_of_date=date(2026, 11, 19), version=1, result=RESULT, inputs={}, created_at=AT)
    out = curve_out(ENTITY, snap, None, date(2026, 11, 23))
    assert out["kind"] == "history" and out["version"] == 1 and out["stale"] is True and out["conventions"] == "SPFI_STD"
