"""Payment schedules of CDS trades: standard CDS dates or a swap-style generator, optional rebate.

Standard dates: coupon dates 20 Mar/Jun/Sep/Dec, Following, accrual dates move with payments.
Own schedule (like an interest-rate swap leg): frequency, generation direction (backward from the
expiration — the stub at the front, or forward from the start — the stub at the end), short or long
stub, roll day, business-day convention, calendar, day count.

Rebate: with a rebate the first coupon covers the whole regular period containing the effective date
(the period starts at the previous coupon date) and the seller returns the accrual from that date to the
effective date at settlement; without it the first period starts at the effective date (a stub).
The last period includes the expiration date (CDS convention) unless switched off.
"""
from __future__ import annotations

import calendar as _calendar
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

from .calendar import BusinessCalendar
from .isda import DAY_COUNTS, Period, Schedule, cds_date_after, cds_date_on_or_before

BDC = ("following", "modified_following", "preceding", "unadjusted")
FREQUENCIES = (1, 3, 6, 12)
_MAX_PERIODS = 600


@dataclass
class ScheduleParams:
    mode: str = "standard"  # standard | custom
    frequency_months: int = 3
    direction: str = "backward"  # backward (from expiration) | forward (from the effective date)
    stub: str = "short"  # short | long
    roll_day: int | None = None  # default: day of the expiration (backward) or of the effective date (forward)
    bdc: str = "following"
    adjust_accrual: bool = True  # accrual dates move with payment dates
    calendar: str = "SPFI"
    day_count: str = "ACT/ACT.ISDA"
    last_period_inclusive: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScheduleParams":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__ and v is not None}
        p = cls(**known)
        if p.mode == "standard":  # CDS dates: quarterly 20th, Following, accrual dates move with payments
            p.frequency_months, p.direction, p.roll_day, p.bdc, p.adjust_accrual = 3, "backward", None, "following", True
        p.validate()
        return p

    def validate(self) -> None:
        if self.mode not in ("standard", "custom"):
            raise ValueError("Режим расписания: standard или custom")
        if self.frequency_months not in FREQUENCIES:
            raise ValueError("Частота: 1, 3, 6 или 12 месяцев")
        if self.direction not in ("backward", "forward"):
            raise ValueError("Направление генерации: backward или forward")
        if self.stub not in ("short", "long"):
            raise ValueError("Неполный период: short или long")
        if self.roll_day is not None and not 1 <= int(self.roll_day) <= 31:
            raise ValueError("День ролла — от 1 до 31")
        if self.bdc not in BDC:
            raise ValueError(f"Перенос дат: {', '.join(BDC)}")
        if self.day_count not in DAY_COUNTS:
            raise ValueError(f"Коэффициент дней: {', '.join(DAY_COUNTS)}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def adjust(d: date, bdc: str, cal: BusinessCalendar) -> date:
    if bdc == "unadjusted" or cal.is_business_day(d):
        return d
    if bdc == "preceding":
        return _preceding(d, cal)
    f = cal.following(d)
    if bdc == "modified_following" and f.month != d.month:
        return _preceding(d, cal)
    return f


def _preceding(d: date, cal: BusinessCalendar) -> date:
    x = d
    for _ in range(400):
        x -= timedelta(days=1)
        if cal.is_business_day(x):
            return x
    raise ValueError("В календаре нет рабочих дней")


def _with_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, _calendar.monthrange(year, month)[1]))


def _shift(anchor: date, months: int, day: int) -> date:
    d = anchor + relativedelta(months=months)
    return _with_day(d.year, d.month, day)


def _grid(start: date, maturity: date, p: ScheduleParams) -> list[date]:
    """Unadjusted regular coupon dates covering [start; maturity], ascending, maturity included."""
    if p.mode == "standard":
        out, d = [], cds_date_on_or_before(start)
        while d < maturity and len(out) < _MAX_PERIODS:
            out.append(d)
            d = cds_date_after(d)
        return out + [maturity]
    f = p.frequency_months
    if p.direction == "backward":
        day = p.roll_day or maturity.day
        out, k = [maturity], 1
        while len(out) < _MAX_PERIODS:
            d = _shift(maturity, -f * k, day)
            out.append(d)
            if d <= start:
                break
            k += 1
        return sorted(set(out))
    day = p.roll_day or start.day
    out, k = [], 0
    while len(out) < _MAX_PERIODS:
        d = _shift(start, f * k, day)
        if d >= maturity:
            break
        out.append(d)
        k += 1
    return out + [maturity]


def generate(trade_date: date, effective: date, maturity: date, rebate: bool, p: ScheduleParams, cal: BusinessCalendar,
             rebate_mode: str = "spec", settle: date | None = None) -> Schedule:
    """Accrual periods with payment dates and accrual fractions (the last one with the expiration day if inclusive)."""
    if maturity <= effective:
        raise ValueError("Дата экспирации должна быть позже даты начала срока")
    dc = DAY_COUNTS[p.day_count]
    grid = _grid(effective, maturity, p)
    if rebate:
        start_unadj = max(d for d in grid if d <= effective) if any(d <= effective for d in grid) else effective
        first_start = adjust(start_unadj, p.bdc, cal) if p.adjust_accrual else start_unadj
        if first_start > effective:  # the adjusted coupon date fell after the effective date
            first_start = start_unadj
        ends = [d for d in grid if d > effective]
    else:
        start_unadj = first_start = effective
        ends = [d for d in grid if d > effective]
        if p.stub == "long" and len(ends) > 1:
            if p.mode == "standard" or p.direction == "backward":
                if effective not in grid:  # a real front stub: merge it with the first regular period
                    ends = ends[1:]
            else:
                day = p.roll_day or effective.day
                if _shift(effective, p.frequency_months * (len(grid) - 1), day) != maturity:  # a real back stub
                    ends = ends[:-2] + [maturity]
    periods: list[Period] = []
    s = first_start
    for i, e_unadj in enumerate(ends):
        last = i == len(ends) - 1
        if last:
            e_acc = maturity  # the scheduled termination date is not adjusted for accrual
        else:
            e_acc = adjust(e_unadj, p.bdc, cal) if p.adjust_accrual else e_unadj
            if e_acc <= s:
                continue
        pay = adjust(e_unadj if not last else maturity, p.bdc, cal)
        frac = dc(s, e_acc + timedelta(days=1)) if last and p.last_period_inclusive else dc(s, e_acc)
        periods.append(Period(s, e_acc, pay, frac))
        s = e_acc
    rebate_frac, r_start, r_end = 0.0, None, None
    if rebate and first_start < effective:
        r_start, r_end = first_start, effective
        if rebate_mode == "unadjusted_start":
            r_start = start_unadj
        elif rebate_mode == "inclusive_end":
            r_end = effective + timedelta(days=1)
        rebate_frac = dc(r_start, r_end)
    return Schedule(trade_date, effective, first_start, maturity, periods, rebate_frac, settle or effective, r_start, r_end)


def schedule_from_rows(trade_date: date, effective: date, maturity: date, rows: list[dict[str, Any]], rebate_frac: float,
                       settle: date, rebate_start: date | None, rebate_end: date | None) -> Schedule:
    """The stored (possibly hand-edited) schedule of a trade version as an engine Schedule."""
    periods = [Period(date.fromisoformat(str(r["accrual_start"])), date.fromisoformat(str(r["accrual_end"])),
                      date.fromisoformat(str(r["pay_date"])), float(r["year_frac"])) for r in rows]
    first = periods[0].accrual_start if periods else effective
    return Schedule(trade_date, effective, first, maturity, periods, rebate_frac, settle, rebate_start, rebate_end)


def rows_of(sched: Schedule, notional: float, coupon: float, last_inclusive: bool = True) -> list[dict[str, Any]]:
    n = len(sched.periods)
    return [{"accrual_start": p.accrual_start.isoformat(), "accrual_end": p.accrual_end.isoformat(), "pay_date": p.pay_date.isoformat(),
             "days": (p.accrual_end - p.accrual_start).days + (1 if i == n - 1 and last_inclusive else 0),
             "year_frac": p.year_frac, "amount": notional * coupon * p.year_frac, "manual": False}
            for i, p in enumerate(sched.periods)]
