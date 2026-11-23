"""Business-day calendars.

Weekdays are business days by default; exceptions (holidays on weekdays and working weekends)
are maintained by operators. A calendar may extend a base one: the SPFI calendar is the Moscow
calendar (RUB settlement days) plus days without SPFI clearing sessions.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

_MAX_SCAN = 400  # a longer run of holidays means a broken calendar, not a real request


@dataclass(frozen=True)
class BusinessCalendar:
    code: str
    exceptions: Mapping[date, bool] = field(default_factory=dict)  # date -> is business day
    base: "BusinessCalendar | None" = None

    def is_business_day(self, d: date) -> bool:
        if d in self.exceptions:
            return self.exceptions[d]
        if self.base is not None:
            return self.base.is_business_day(d)
        return d.weekday() < 5

    def following(self, d: date) -> date:
        for _ in range(_MAX_SCAN):
            if self.is_business_day(d):
                return d
            d += timedelta(days=1)
        raise ValueError(f"Календарь {self.code}: нет рабочих дней — проверьте исключения")

    def add_business_days(self, d: date, n: int) -> date:
        step = 1 if n >= 0 else -1
        k = 0
        for _ in range(_MAX_SCAN + abs(n) * 7):
            if k == abs(n):
                return d
            d += timedelta(days=step)
            if self.is_business_day(d):
                k += 1
        raise ValueError(f"Календарь {self.code}: нет рабочих дней — проверьте исключения")


WEEKDAYS_ONLY = BusinessCalendar("WEEKDAYS")
