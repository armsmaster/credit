"""Market conventions: tenors, day counts, swap schedules.

Simplifications (deliberate, documented in docs/curves-math.md):
* effective date = curve date (no spot lag), no business-day adjustment / holiday calendars;
* month arithmetic is calendar with end-of-month clamping (31 Jan + 1M = 28/29 Feb).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from dateutil.relativedelta import relativedelta


class DayCount(str, Enum):
    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"

    def year_fraction(self, start: date, end: date) -> float:
        days = (end - start).days
        return days / (365.0 if self is DayCount.ACT_365F else 360.0)


def add_months(d: date, months: int) -> date:
    return d + relativedelta(months=months)


_TENOR_RE = re.compile(r"^\s*(\d+)\s*([DWMY])\s*$", re.IGNORECASE)
_TENOR_ALIASES = {"ON": "1D", "O/N": "1D", "TN": "2D", "T/N": "2D", "SN": "3D", "S/N": "3D"}


@dataclass(frozen=True)
class Tenor:
    count: int
    unit: str  # D, W, M, Y

    @classmethod
    def parse(cls, raw: str) -> "Tenor":
        text = _TENOR_ALIASES.get(str(raw).strip().upper(), str(raw))
        m = _TENOR_RE.match(text)
        if not m:
            raise ValueError(f"Некорректный тенор: {raw!r}. Ожидается формат 1W, 3M, 2Y, ON")
        count, unit = int(m.group(1)), m.group(2).upper()
        if count <= 0:
            raise ValueError(f"Тенор должен быть положительным: {raw!r}")
        return cls(count, unit)

    @property
    def months(self) -> int | None:
        """Whole number of months for M/Y tenors, None for D/W."""
        if self.unit == "M":
            return self.count
        if self.unit == "Y":
            return 12 * self.count
        return None

    def maturity(self, start: date) -> date:
        if self.unit == "D":
            return start + timedelta(days=self.count)
        if self.unit == "W":
            return start + timedelta(weeks=self.count)
        return add_months(start, self.months)  # type: ignore[arg-type]

    def sort_key(self) -> float:
        per_unit = {"D": 1 / 30.4375, "W": 7 / 30.4375, "M": 1.0, "Y": 12.0}
        return self.count * per_unit[self.unit]

    def __str__(self) -> str:
        return f"{self.count}{self.unit}"


@dataclass(frozen=True)
class SwapConventions:
    """Conventions of the quoted par instruments (OIS / key-rate swaps).

    Up to ``single_payment_max_months`` the swap has one fixed payment at maturity
    (money-market style), beyond that the fixed leg pays every ``fixed_frequency_months``
    with a short front stub. The compounded overnight leg is worth 1 - DF(T).
    """

    day_count: DayCount = DayCount.ACT_365F
    fixed_frequency_months: int = 12
    single_payment_max_months: int = 12

    def payment_dates(self, start: date, tenor: Tenor) -> list[date]:
        months = tenor.months
        if months is None or months <= self.single_payment_max_months:
            return [tenor.maturity(start)]
        freq = self.fixed_frequency_months
        offsets = list(range(months, 0, -freq))[::-1]
        return [add_months(start, k) for k in offsets]


def months_to_cover(start: date, end: date) -> int:
    """Smallest k such that start + k months >= end."""
    k = max(0, (end.year - start.year) * 12 + (end.month - start.month) - 1)
    while add_months(start, k) < end:
        k += 1
    return max(k, 1)


__all__ = ["DayCount", "Tenor", "SwapConventions", "add_months", "months_to_cover"]
