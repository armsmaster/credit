"""Service-wide settings editable by operators (stored in credit.settings, journaled)."""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cds_common.audit import AuditLog, diff
from cds_common.auth import Principal

from .models import Setting

audit = AuditLog("credit")

DEFAULTS: dict[str, Any] = {
    # interest-rate curve (code in the curves service) projecting each floating index
    "index_curves": {"KEYRATE": "RUB-KEYRATE", "RUONIA": "RUB-RUONIA-OIS"},
    # liquidity statistics and recommendation thresholds
    "liquidity": {"window_days": 90, "min_avg_turnover": 1_000_000, "min_avg_trades": 3},
    # DEMA of hourly closes: half_life_hours = null -> chosen automatically
    "dema": {"half_life_hours": None, "lookback_days": 90},
    # CDS pricing; open questions to NCC / Sber of the specification are settings, not code
    "cds": {
        "recovery_quote": 0.40,  # R_q of the QS <-> PUF convention (not a recovery estimate)
        "tenors": ["6M", "1Y", "2Y", "3Y", "4Y", "5Y"],
        "coupons_bp": [100, 500],
        "day_count": "ACT/ACT.ISDA",  # coupon and rebate accrual (ACT/360 — international standard)
        "protection_from_effective": False,  # False: ISDA step-in T+1 calendar day
        "rebate_mode": "spec",  # spec (38 days in the MOEX example) | unadjusted_start | inclusive_end (39 days)
        "sber_adjust_period_ends": True,
        "sber_last_period_inclusive": True,
        "spfi_calendar": "SPFI",
        "sber_calendar": "MOSCOW",
    },
}
EDITABLE = tuple(DEFAULTS)
COMPUTED = ("dema_auto",)  # written by the price refresh task


def load(session: Session) -> dict[str, Any]:
    out = copy.deepcopy(DEFAULTS)
    for row in session.scalars(select(Setting)):
        if row.key in DEFAULTS and isinstance(row.value, dict):
            out[row.key] = {**DEFAULTS[row.key], **row.value}
        else:
            out[row.key] = row.value
    return out


def save(session: Session, key: str, value: Any, actor: Principal, *, journal: bool = True) -> None:
    row = session.get(Setting, key)
    before = row.value if row else DEFAULTS.get(key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_by=actor.username))
    else:
        row.value = value
        row.updated_by = actor.username
    if journal:
        changes = diff(before if isinstance(before, dict) else {"value": before}, value if isinstance(value, dict) else {"value": value})
        if changes:
            audit.record(session, actor=actor, entity_type="settings", entity_id=key, action="update",
                         summary=f"Изменены настройки кредитного сервиса: {key}", changes=changes)
