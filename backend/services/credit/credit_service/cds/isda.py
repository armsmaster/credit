"""ISDA CDS Standard Model with the SPFI (CCP-cleared, NCC) and Sber OTC conventions.

A port of the reference implementation attached to the specification (cds_pricer.py,
«Спецификация прайсинга CDS» of 23.11.2026); tests compare this module with it.

Units: amounts in fractions of notional (1.0 = 100%), rates as decimals (0.01 = 100 bp),
model time in years ACT/365F from the valuation date. Everything from the protection BUYER's side.
Accrual fractions of coupons and rebate use ACT/ACT (ISDA) by default.

Integrals are exact on piecewise-constant hazard λ and forward f: on [a, b] with μ = λ + f
    ∫ P λ Q dt     = Q(a) P(a) λ/μ (1 − e^{−μΔ})
    ∫ α(t) P λ Q dt = Q(a) P(a) [α_a λ/μ (1 − e^{−μΔ}) + κ λ (1 − e^{−μΔ}(1 + μΔ)) / μ²]
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from scipy.optimize import brentq

from .calendar import BusinessCalendar

# ---------------------------------------------------------------------------
# Day counts and CDS dates
# ---------------------------------------------------------------------------


def days_in_year(y: int) -> int:
    return 366 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 365


def yf_act_act_isda(d1: date, d2: date) -> float:
    """ACT/ACT (ISDA): days of each calendar year divided by that year's length."""
    if d2 <= d1:
        return 0.0
    total, cur = 0.0, d1
    while cur < d2:
        nxt = min(date(cur.year + 1, 1, 1), d2)
        total += (nxt - cur).days / days_in_year(cur.year)
        cur = nxt
    return total


def yf_act365f(d1: date, d2: date) -> float:
    return (d2 - d1).days / 365.0


def yf_act360(d1: date, d2: date) -> float:
    return (d2 - d1).days / 360.0


DAY_COUNTS: dict[str, Callable[[date, date], float]] = {
    "ACT/ACT.ISDA": yf_act_act_isda,
    "ACT/365F": yf_act365f,
    "ACT/360": yf_act360,
}

CDS_MONTHS = (3, 6, 9, 12)
TENOR_MONTHS = {"6M": 6, "1Y": 12, "2Y": 24, "3Y": 36, "4Y": 48, "5Y": 60, "7Y": 84, "10Y": 120}


def cds_date_on_or_before(d: date) -> date:
    """Latest unadjusted CDS date (20 Mar/Jun/Sep/Dec) <= d."""
    y, m = d.year, d.month
    for _ in range(5):
        if m in CDS_MONTHS and date(y, m, 20) <= d:
            return date(y, m, 20)
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    raise RuntimeError("unreachable")


def cds_date_after(d: date) -> date:
    """Earliest unadjusted CDS date strictly after d."""
    y, m = d.year, d.month
    for _ in range(5):
        if m in CDS_MONTHS and date(y, m, 20) > d:
            return date(y, m, 20)
        m += 1
        if m == 13:
            m, y = 1, y + 1
    raise RuntimeError("unreachable")


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, d.day)


def anchor_cds_date(effective: date) -> date:
    """Anchor CDS date of the SPFI specification (semi-annual roll): 20 Dec for E in [20 Sep; 19 Mar],
    20 Jun for E in [20 Mar; 19 Sep]."""
    y = effective.year
    if date(y, 3, 20) <= effective <= date(y, 9, 19):
        return date(y, 6, 20)
    if effective >= date(y, 9, 20):
        return date(y, 12, 20)
    return date(y - 1, 12, 20)


def standard_maturity(effective: date, tenor: str) -> date:
    return add_months(anchor_cds_date(effective), TENOR_MONTHS[tenor])


# ---------------------------------------------------------------------------
# Conventions and schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Conventions:
    name: str
    effective_lag_bd: int  # effective date = T + lag business days
    accrual_from_prev_cds_date: bool  # True: first period from the previous adjusted CDS date + rebate
    day_count: str = "ACT/ACT.ISDA"
    last_period_inclusive: bool = True  # the last period includes the scheduled termination date
    upfront_settle_lag_bd: int = 1  # initial payment date = T + lag business days
    step_in_lag_cd: int = 1  # ISDA step-in = T + 1 calendar day
    protection_from_effective: bool = False  # True: protection from the effective date (alternative to ISDA)
    adjust_period_ends: bool = True  # period ends move with payment dates (Following)
    rebate_mode: str = "spec"  # spec: S (adjusted) incl. → E excl.; unadjusted_start: from 20th; inclusive_end: E incl.


SPFI = Conventions("SPFI_STD", effective_lag_bd=1, accrual_from_prev_cds_date=True, upfront_settle_lag_bd=1)
SBER_OTC = Conventions("SBER_OTC", effective_lag_bd=0, accrual_from_prev_cds_date=False, upfront_settle_lag_bd=0)
PROFILES = {"SPFI_STD": SPFI, "SBER_OTC": SBER_OTC}


@dataclass
class Period:
    accrual_start: date
    accrual_end: date  # for the last period: the scheduled termination date (inclusive)
    pay_date: date
    year_frac: float  # accrual fraction, last day included for the last period


@dataclass
class Schedule:
    trade_date: date
    effective_date: date
    first_period_start: date
    maturity: date
    periods: list[Period]
    rebate_year_frac: float  # ACT/ACT from the first period start (incl.) to the effective date (excl.)
    upfront_settle_date: date
    rebate_start: date | None = None
    rebate_end: date | None = None  # exclusive


def build_schedule(trade_date: date, maturity: date, conv: Conventions, cal: BusinessCalendar) -> Schedule:
    dc = DAY_COUNTS[conv.day_count]
    eff = cal.add_business_days(trade_date, conv.effective_lag_bd) if conv.effective_lag_bd else trade_date
    if conv.accrual_from_prev_cds_date:
        start = cal.following(cds_date_on_or_before(eff))
        if start > eff:  # cannot happen when eff is a business day; kept for robustness
            start = cal.following(cds_date_on_or_before(eff - timedelta(days=1)))
    else:
        start = eff
    ends: list[date] = []
    u = cds_date_after(start)
    while u < maturity:
        a = cal.following(u) if conv.adjust_period_ends else u
        if a > start:
            ends.append(a)
        u = cds_date_after(u)
    periods: list[Period] = []
    s = start
    for e in ends:
        if e >= maturity:
            break
        pay = cal.following(e)
        periods.append(Period(s, e, pay, dc(s, e)))
        s = e
    last_end_excl = maturity + timedelta(days=1) if conv.last_period_inclusive else maturity
    periods.append(Period(s, maturity, cal.following(maturity), dc(s, last_end_excl)))
    rebate, r_start, r_end = 0.0, None, None
    if conv.accrual_from_prev_cds_date:
        r_start, r_end = start, eff
        if conv.rebate_mode == "unadjusted_start":
            r_start = cds_date_on_or_before(eff)
        elif conv.rebate_mode == "inclusive_end":
            r_end = eff + timedelta(days=1)
        rebate = dc(r_start, r_end)
    settle = cal.add_business_days(trade_date, conv.upfront_settle_lag_bd) if conv.upfront_settle_lag_bd else trade_date
    return Schedule(trade_date, eff, start, maturity, periods, rebate, settle, r_start, r_end)


# ---------------------------------------------------------------------------
# Curves
# ---------------------------------------------------------------------------


@dataclass
class DiscountCurve:
    """DF at node dates, log-linear interpolation (piecewise-flat forward), flat forward extrapolation."""

    base: date
    dates: list[date]
    dfs: list[float]
    t: list[float] = field(init=False)
    logdf: list[float] = field(init=False)

    def __post_init__(self):
        self.t = [yf_act365f(self.base, d) for d in self.dates]
        self.logdf = [math.log(x) for x in self.dfs]
        if not self.t or self.t[0] > 0:
            self.t.insert(0, 0.0)
            self.logdf.insert(0, 0.0)

    @classmethod
    def flat(cls, base: date, rate_cc: float, years: int = 40) -> "DiscountCurve":
        ds = [base + timedelta(days=365 * k) for k in range(1, years + 1)]
        return cls(base, ds, [math.exp(-rate_cc * yf_act365f(base, d)) for d in ds])

    def knots(self) -> list[float]:
        return list(self.t)

    def df_t(self, t: float) -> float:
        ts, ls = self.t, self.logdf
        if t <= 0:
            return 1.0
        i = bisect_right(ts, t) - 1
        if i >= len(ts) - 1:
            i = len(ts) - 2
        w = (t - ts[i]) / (ts[i + 1] - ts[i])
        return math.exp(ls[i] + w * (ls[i + 1] - ls[i]))

    def df(self, d: date) -> float:
        return self.df_t(yf_act365f(self.base, d))


@dataclass
class HazardCurve:
    """Piecewise-constant intensity: lambda_i acts on (t_{i-1}, t_i], the last one flat to the right."""

    base: date
    times: list[float]
    lambdas: list[float]

    @classmethod
    def from_dates(cls, base: date, dates: Sequence[date], lambdas: Sequence[float]) -> "HazardCurve":
        return cls(base, [yf_act365f(base, d) for d in dates], list(lambdas))

    @classmethod
    def flat(cls, base: date, lam: float) -> "HazardCurve":
        return cls(base, [100.0], [lam])

    def knots(self) -> list[float]:
        return list(self.times)

    def lam(self, t: float) -> float:
        i = bisect_right(self.times, t - 1e-14)
        return self.lambdas[min(i, len(self.lambdas) - 1)]

    def survival_t(self, t: float) -> float:
        acc, prev = 0.0, 0.0
        for ti, li in zip(self.times, self.lambdas):
            if t <= ti:
                return math.exp(-(acc + li * (t - prev)))
            acc += li * (ti - prev)
            prev = ti
        return math.exp(-(acc + self.lambdas[-1] * (t - prev)))

    def survival(self, d: date) -> float:
        return self.survival_t(yf_act365f(self.base, d))


# ---------------------------------------------------------------------------
# Legs (exact integrals on piecewise-constant lambda and f)
# ---------------------------------------------------------------------------


def _grid(a: float, b: float, *knot_lists: Iterable[float]) -> list[float]:
    pts = {a, b}
    for ks in knot_lists:
        for k in ks:
            if a < k < b:
                pts.add(k)
    return sorted(pts)


def _year_starts(base: date, d1: date, d2: date) -> list[float]:
    out, y = [], d1.year + 1
    while date(y, 1, 1) < d2:
        out.append(yf_act365f(base, date(y, 1, 1)))
        y += 1
    return out


def _accrual_slope(day_count: str, d: date) -> float:
    """d(accrual fraction)/d(model time ACT/365F) on day d."""
    if day_count == "ACT/ACT.ISDA":
        return 365.0 / days_in_year(d.year)
    if day_count == "ACT/360":
        return 365.0 / 360.0
    return 1.0


def protection_pieces(t_start: float, t_end: float, dc: DiscountCurve, hc: HazardCurve) -> list[dict[str, float]]:
    """Sub-intervals of the protection integral ∫ P λ Q dt (before multiplying by 1 − R)."""
    if t_end <= t_start:
        return []
    out = []
    g = _grid(t_start, t_end, dc.knots(), hc.knots())
    for a, b in zip(g[:-1], g[1:]):
        lam = hc.lam(0.5 * (a + b))
        qa, pa = hc.survival_t(a), dc.df_t(a)
        f = -math.log(dc.df_t(b) / pa) / (b - a)
        mu, dt = lam + f, b - a
        e = math.exp(-mu * dt)
        value = qa * pa * (lam / mu * (1.0 - e) if abs(mu) > 1e-12 else lam * dt)
        out.append({"a": a, "b": b, "lam": lam, "f": f, "q_a": qa, "p_a": pa, "value": value})
    return out


def protection_leg(base: date, t_start: float, t_end: float, recovery: float, dc: DiscountCurve, hc: HazardCurve) -> float:
    """(1 − R) ∫_{t_start}^{t_end} DF(t) dP(t), per unit notional, PV at base."""
    return (1.0 - recovery) * sum(p["value"] for p in protection_pieces(t_start, t_end, dc, hc))


def aod_pieces(base: date, p: Period, t_from: float, t_to: float, day_count: str, dc: DiscountCurve,
               hc: HazardCurve) -> list[dict[str, float]]:
    """Sub-intervals of ∫ α(t) DF(t) dP(t) over a coupon period (per unit coupon and notional)."""
    if t_to <= t_from:
        return []
    t_s = yf_act365f(base, p.accrual_start)
    yrs = _year_starts(base, p.accrual_start, p.accrual_end + timedelta(days=2))
    g = _grid(t_from, t_to, dc.knots(), hc.knots(), yrs)
    dcf = DAY_COUNTS[day_count]
    out = []
    for a, b in zip(g[:-1], g[1:]):
        da = base + timedelta(days=round(a * 365))
        alpha_a = dcf(p.accrual_start, da) if a > t_s else 0.0
        alpha_a += (a - yf_act365f(base, da)) * _accrual_slope(day_count, da)
        kappa = _accrual_slope(day_count, da)
        lam = hc.lam(0.5 * (a + b))
        qa, pa = hc.survival_t(a), dc.df_t(a)
        f = -math.log(dc.df_t(b) / pa) / (b - a)
        mu, dt = lam + f, b - a
        if abs(mu) < 1e-10:
            i0, i1 = lam * dt, lam * dt * dt / 2
        else:
            e = math.exp(-mu * dt)
            i0 = lam / mu * (1.0 - e)
            i1 = lam * (1.0 - e * (1.0 + mu * dt)) / (mu * mu)
        out.append({"a": a, "b": b, "alpha_a": alpha_a, "kappa": kappa, "lam": lam, "f": f, "q_a": qa, "p_a": pa,
                    "i0": i0, "i1": i1, "value": qa * pa * (alpha_a * i0 + kappa * i1)})
    return out


def accrual_on_default(base: date, p: Period, t_from: float, t_to: float, day_count: str, dc: DiscountCurve,
                       hc: HazardCurve) -> float:
    return sum(x["value"] for x in aod_pieces(base, p, t_from, t_to, day_count, dc, hc))


@dataclass
class LegValues:
    protection: float  # PV of protection (per unit notional, at the valuation date)
    rpv01_dirty: float  # PV of the premium leg per unit coupon, incl. the full first coupon and AoD
    accrued_frac: float  # rebate / accrued fraction (for the clean price)
    df_settle: float  # DF to the initial payment date
    protection_start: date = None  # type: ignore[assignment]
    step_in: date = None  # type: ignore[assignment]
    coupons: list[dict[str, Any]] = field(default_factory=list)  # per period: α, DF(pay), Q(obs), AoD


def cds_legs(sched: Schedule, valuation: date, recovery: float, conv: Conventions, dc: DiscountCurve,
             hc: HazardCurve) -> LegValues:
    one = timedelta(days=1)
    step_in = valuation + timedelta(days=conv.step_in_lag_cd)
    if conv.protection_from_effective:
        prot_start = max(valuation, step_in - one, sched.effective_date - one)
    else:
        prot_start = max(valuation, step_in - one)
    t_ps = yf_act365f(valuation, prot_start)
    t_mat = yf_act365f(valuation, sched.maturity)
    prot = protection_leg(valuation, t_ps, t_mat, recovery, dc, hc)

    rpv, coupons = 0.0, []
    for p in sched.periods:
        is_last = p is sched.periods[-1]
        end_excl = p.accrual_end + one if (is_last and conv.last_period_inclusive) else p.accrual_end
        if end_excl <= step_in:
            continue
        obs = end_excl - one  # survival observed on the last day of the period
        df_pay, q_obs = dc.df(p.pay_date), hc.survival(obs)
        t_from = yf_act365f(valuation, max(p.accrual_start, prot_start))
        t_to = yf_act365f(valuation, obs)
        aod = accrual_on_default(valuation, p, t_from, t_to, conv.day_count, dc, hc)
        rpv += p.year_frac * df_pay * q_obs + aod
        coupons.append({"period": p, "observation": obs, "df_pay": df_pay, "q_obs": q_obs, "aod": aod})

    if valuation <= sched.trade_date:
        acc = sched.rebate_year_frac
    else:  # accrued of an existing trade: from the start of the period containing the step-in date (ISDA)
        last = sched.periods[-1] if sched.periods else None
        cur = next((p for p in sched.periods
                    if p.accrual_start <= step_in < (p.accrual_end + one if p is last and conv.last_period_inclusive else p.accrual_end)), None)
        acc = DAY_COUNTS[conv.day_count](cur.accrual_start, step_in) if cur else 0.0
    settle = sched.upfront_settle_date if valuation <= sched.trade_date else valuation
    return LegValues(prot, rpv, acc, dc.df(settle), prot_start, step_in, coupons)


def price_legs(L: LegValues, coupon: float) -> dict[str, float]:
    pv_dirty = L.protection - coupon * L.rpv01_dirty
    rpv01_clean = L.rpv01_dirty - L.accrued_frac * L.df_settle
    initial_payment = pv_dirty / L.df_settle
    return {
        "pv_dirty": pv_dirty, "protection": L.protection, "rpv01_dirty": L.rpv01_dirty, "rpv01_clean": rpv01_clean,
        "accrued_frac": L.accrued_frac, "df_settle": L.df_settle, "initial_payment": initial_payment,
        "puf": initial_payment + coupon * L.accrued_frac, "par_spread": L.protection / rpv01_clean,
    }


def price(sched: Schedule, valuation: date, coupon: float, recovery: float, conv: Conventions, dc: DiscountCurve,
          hc: HazardCurve) -> dict[str, float]:
    """Per unit notional, protection BUYER's side.
    pv_dirty — PV of future flows at valuation (MtM); puf — clean upfront (+ = buyer pays) at settlement;
    initial_payment = puf − coupon·accrued (+ = buyer pays)."""
    return price_legs(cds_legs(sched, valuation, recovery, conv, dc, hc), coupon)


# ---------------------------------------------------------------------------
# Solvers, conversions, bootstrap
# ---------------------------------------------------------------------------

LAMBDA_BRACKET = (1e-10, 5.0)


class BracketError(ValueError):
    """No hazard rate in [1e-10; 5] reproduces the quote (arbitrage or an erroneous quote)."""


def _solve(f: Callable[[float], float]) -> float:
    lo, hi = LAMBDA_BRACKET
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        raise BracketError("корень не взят в вилку [1e-10; 5]: котировка вне допустимого диапазона")
    return brentq(f, lo, hi, xtol=1e-15, rtol=1e-15, maxiter=200)


def flat_hazard_for_puf(sched: Schedule, valuation: date, coupon: float, recovery: float, conv: Conventions,
                        dc: DiscountCurve, puf_target: float) -> float:
    return _solve(lambda lam: price(sched, valuation, coupon, recovery, conv, dc, HazardCurve.flat(valuation, lam))["puf"] - puf_target)


def qs_to_puf(sched: Schedule, valuation: date, coupon: float, qs: float, conv: Conventions, dc: DiscountCurve,
              recovery_quote: float = 0.40) -> float:
    """ISDA convention: flat curve with par spread = QS; PUF at the standard coupon."""
    lam = flat_hazard_for_puf(sched, valuation, qs, recovery_quote, conv, dc, 0.0)
    return price(sched, valuation, coupon, recovery_quote, conv, dc, HazardCurve.flat(valuation, lam))["puf"]


def puf_to_qs_detail(sched: Schedule, valuation: date, coupon: float, puf: float, conv: Conventions, dc: DiscountCurve,
                     recovery_quote: float = 0.40) -> tuple[float, float]:
    """(QS, flat lambda): the flat curve with PUF(coupon) = puf, QS = its par spread."""
    lam = flat_hazard_for_puf(sched, valuation, coupon, recovery_quote, conv, dc, puf)
    return price(sched, valuation, coupon, recovery_quote, conv, dc, HazardCurve.flat(valuation, lam))["par_spread"], lam


def puf_to_qs(sched: Schedule, valuation: date, coupon: float, puf: float, conv: Conventions, dc: DiscountCurve,
              recovery_quote: float = 0.40) -> float:
    return puf_to_qs_detail(sched, valuation, coupon, puf, conv, dc, recovery_quote)[0]


@dataclass
class Quote:
    maturity: date
    kind: str  # PUF (upfront at coupon), QS (quoted spread), PAR (par spread)
    value: float
    coupon: float = 0.01


def bootstrap(valuation: date, trade_date: date, quotes: Sequence[Quote], recovery: float, conv: Conventions,
              cal: BusinessCalendar, dc: DiscountCurve, recovery_quote: float = 0.40) -> HazardCurve:
    """Sequential bootstrap of piecewise-constant lambda by increasing maturity."""
    times: list[float] = []
    lams: list[float] = []
    for q in sorted(quotes, key=lambda q: q.maturity):
        sched = build_schedule(trade_date, q.maturity, conv, cal)
        if q.kind == "QS":
            target, cpn = qs_to_puf(sched, valuation, q.coupon, q.value, conv, dc, recovery_quote), q.coupon
        elif q.kind == "PAR":
            target, cpn = 0.0, q.value
        else:
            target, cpn = q.value, q.coupon
        t_i = yf_act365f(valuation, q.maturity)
        lams.append(_solve(lambda lam: price(sched, valuation, cpn, recovery, conv, dc,
                                             HazardCurve(valuation, times + [t_i], lams + [lam]))["puf"] - target))
        times.append(t_i)
    return HazardCurve(valuation, times, lams)


def with_settings(base: Conventions, **overrides: Any) -> Conventions:
    known = {k: v for k, v in overrides.items() if k in Conventions.__dataclass_fields__ and v is not None}
    return replace(base, **known)
