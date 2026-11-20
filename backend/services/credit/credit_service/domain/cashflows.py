"""Projected cash flows of a bond after the settlement date.

Conventions (Russian bond market, see docs/credit-math.md):
* coupon of a period = notional × rate × days / 365, paid at the period end;
* a coupon belongs to the buyer if the record date (or the day before payment) is not
  earlier than the settlement date;
* put offer: the bond is priced to the offer — coupons up to the offer date and redemption of
  the outstanding notional at the offer price (the issuer resets the coupon at the offer, so the
  market prices such bonds to the offer);
* amortizations from the ISS schedule; the final redemption takes whatever is left, so the sum
  of redemptions always equals the outstanding notional at settlement;
* floating coupons without a known value: simple forward of the index curve over the period
  (shifted back by the lag) plus the margin; the already elapsed part of the current period is
  approximated by the forward from the curve date;
* indexed notional (VEB.RF linkers): N(t) = N(S) · DF_RUONIA(S) / DF_RUONIA(t) — daily compounded
  RUONIA projected by the OIS RUONIA curve; coupons and redemption are paid on the indexed notional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .bonds import BondKind, BondProfile, RateIndex
from .rates import RateCurve


class CashflowError(ValueError):
    pass


@dataclass
class Cashflow:
    date: date
    kind: str  # coupon | amortization | redemption
    amount: float  # RUB per bond
    rate_pct: float | None = None
    projected: bool = False  # True when the amount is a model projection (floating / linker)
    # how the amount was obtained (shown in the Excel calculation detail)
    source: str = ""  # iss_amount | fixed_rate | floating | linker_coupon | iss_amortization | redemption | linker_redemption
    start: date | None = None  # coupon period start
    days: int | None = None  # coupon period length, days
    notional: float | None = None  # notional the coupon/redemption is paid on (indexed for linkers)
    index_rate_pct: float | None = None  # projected index rate of the period (floating coupons)
    spread_pct: float | None = None  # margin over the index (floating coupons)
    price_pct: float | None = None  # redemption price, % of the outstanding notional

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(), "kind": self.kind, "amount": self.amount, "rate_pct": self.rate_pct,
            "projected": self.projected, "source": self.source, "start": self.start.isoformat() if self.start else None,
            "days": self.days, "notional": self.notional, "index_rate_pct": self.index_rate_pct,
            "spread_pct": self.spread_pct, "price_pct": self.price_pct,
        }


@dataclass
class ProjectedBond:
    secid: str
    curve_date: date
    settle_date: date
    face_settle: float  # notional per bond at settlement (indexed / after amortizations)
    maturity: date  # effective maturity (offer or redemption)
    flows: list[Cashflow] = field(default_factory=list)
    # outstanding notional (recovery claim): step function of non-indexed notional × index factor
    _step_dates: list[date] = field(default_factory=list)
    _step_values: list[float] = field(default_factory=list)
    _index_curve: RateCurve | None = None

    @property
    def days(self) -> np.ndarray:
        return np.array([(f.date - self.curve_date).days for f in self.flows], dtype=float)

    @property
    def amounts(self) -> np.ndarray:
        return np.array([f.amount for f in self.flows], dtype=float)

    def index_factor(self, days: np.ndarray) -> np.ndarray:
        if self._index_curve is None:
            return np.ones_like(np.asarray(days, dtype=float))
        s = (self.settle_date - self.curve_date).days
        c = self._index_curve
        return c.df_days(self.curve_date, s) / c.df_days(self.curve_date, days)

    def notional_at(self, days: np.ndarray) -> np.ndarray:
        """Outstanding notional (RUB per bond) at day offsets from the curve date."""
        days = np.asarray(days, dtype=float)
        step_days = np.array([(d - self.curve_date).days for d in self._step_dates], dtype=float)
        values = np.concatenate([[self.face_settle], np.array(self._step_values, dtype=float)])
        idx = np.searchsorted(step_days, days, side="right")  # 0 = before the first amortization
        return values[idx] * self.index_factor(days)


def _index_forward(p: BondProfile, start: date, end: date, curve_date: date, curves: dict[RateIndex, RateCurve]) -> float:
    """Projected index rate of a coupon period, % (simple ACT/365 forward of the index curve)."""
    if p.coupon_index is None or p.spread_pct is None:
        raise CashflowError(f"{p.secid}: для плавающего купона не заданы индекс и маржа")
    curve = curves.get(p.coupon_index)
    if curve is None:
        raise CashflowError(f"{p.secid}: нет кривой для индекса {p.coupon_index.value}")
    a = max((start - curve_date).days - p.lag_days, 0)
    b = max((end - curve_date).days - p.lag_days, a + 1)
    return curve.simple_forward(curve_date, a, b) * 100.0


def _coupon_rate(p: BondProfile, start: date, end: date, curve_date: date, curves: dict[RateIndex, RateCurve]) -> float:
    return _index_forward(p, start, end, curve_date, curves) + (p.spread_pct or 0.0)


def project(
    p: BondProfile,
    *,
    curve_date: date,
    settle_date: date,
    face_settle: float,
    curves: dict[RateIndex, RateCurve],
) -> ProjectedBond:
    if p.kind is None:
        raise CashflowError(f"{p.secid}: тип облигации не определён")
    maturity = p.effective_maturity
    if maturity is None:
        raise CashflowError(f"{p.secid}: нет даты погашения")
    if maturity <= settle_date:
        raise CashflowError(f"{p.secid}: погашение/оферта {maturity} не позже даты расчётов {settle_date}")
    if face_settle is None or face_settle <= 0:
        raise CashflowError(f"{p.secid}: нет номинала на дату расчётов")
    to_offer = p.offer_date is not None and maturity == p.offer_date
    redemption_price = (p.offer_price if to_offer else 100.0) / 100.0

    index_curve = None
    if p.kind is BondKind.LINKER:
        index_curve = curves.get(p.notional_index or RateIndex.RUONIA)
        if index_curve is None:
            raise CashflowError(f"{p.secid}: нет кривой RUONIA для индексации номинала")
    out = ProjectedBond(p.secid, curve_date, settle_date, face_settle, maturity, _index_curve=index_curve)

    def factor(d: date) -> float:
        return float(out.index_factor(np.array([(d - curve_date).days]))[0])

    # ---- amortizations strictly between settlement and the effective maturity (non-linkers)
    initial = p.initial_face or face_settle
    amort_flows: list[Cashflow] = []
    outstanding = face_settle
    if p.kind is not BondKind.LINKER:
        for a in p.amortizations:
            if a.is_maturity or not (settle_date < a.date < maturity):
                continue
            amount = a.value if a.value is not None else (a.percent or 0.0) / 100.0 * initial
            amount = min(amount, outstanding)
            if amount <= 0:
                continue
            outstanding -= amount
            amort_flows.append(Cashflow(a.date, "amortization", amount, source="iss_amortization"))
            out._step_dates.append(a.date)
            out._step_values.append(outstanding)

    def outstanding_at(d: date) -> float:
        value = face_settle
        for f in amort_flows:
            if f.date <= d:
                value -= f.amount
        return value

    # ---- coupons
    coupon_flows: list[Cashflow] = []
    for i, c in enumerate(p.coupons):
        last_holder_day = c.record or (c.end - timedelta(days=1))
        if c.end > maturity or last_holder_day < settle_date:
            continue
        start = c.start or (p.coupons[i - 1].end if i > 0 else None)
        if start is None:
            raise CashflowError(f"{p.secid}: у купона {c.end} нет даты начала периода")
        days = (c.end - start).days
        notional = outstanding_at(start)
        if p.kind is BondKind.LINKER:
            rate = p.fixed_rate_pct if p.fixed_rate_pct is not None else c.rate
            if rate is None:
                raise CashflowError(f"{p.secid}: неизвестна ставка купона линкера")
            indexed = face_settle * factor(c.end)
            amount = indexed * rate / 100.0 * days / 365.0
            coupon_flows.append(Cashflow(c.end, "coupon", amount, rate, projected=True, source="linker_coupon",
                                         start=start, days=days, notional=indexed))
        elif c.value is not None:
            coupon_flows.append(Cashflow(c.end, "coupon", c.value, c.rate, source="iss_amount", start=start, days=days,
                                         notional=notional))
        elif p.kind is BondKind.FLOATING:
            index = _index_forward(p, start, c.end, curve_date, curves)
            rate = index + (p.spread_pct or 0.0)
            coupon_flows.append(Cashflow(c.end, "coupon", notional * rate / 100.0 * days / 365.0, rate, projected=True,
                                         source="floating", start=start, days=days, notional=notional,
                                         index_rate_pct=index, spread_pct=p.spread_pct))
        else:
            rate = p.fixed_rate_pct if p.fixed_rate_pct is not None else c.rate
            if rate is None:
                raise CashflowError(f"{p.secid}: неизвестна ставка купона {c.end}")
            coupon_flows.append(Cashflow(c.end, "coupon", notional * rate / 100.0 * days / 365.0, rate, source="fixed_rate",
                                         start=start, days=days, notional=notional))

    # ---- final redemption of what is left
    base = outstanding
    if p.kind is BondKind.LINKER:
        base = face_settle * factor(maturity)
    redemption = Cashflow(maturity, "redemption", base * redemption_price, projected=p.kind is BondKind.LINKER,
                          source="linker_redemption" if p.kind is BondKind.LINKER else "redemption",
                          notional=base, price_pct=redemption_price * 100.0)

    out.flows = sorted(coupon_flows + amort_flows + [redemption], key=lambda f: (f.date, f.kind != "coupon"))
    return out


def accrued_interest(p: BondProfile, *, curve_date: date, settle_date: date, face_settle: float,
                     curves: dict[RateIndex, RateCurve]) -> float:
    """Accrued coupon at settlement (fallback when ISS has no value): linear within the period."""
    for i, c in enumerate(p.coupons):
        start = c.start or (p.coupons[i - 1].end if i > 0 else None)
        if start is None or not (start < settle_date <= c.end):
            continue
        days = (c.end - start).days
        if p.kind is BondKind.LINKER:
            rate = p.fixed_rate_pct if p.fixed_rate_pct is not None else c.rate
            amount = face_settle * (rate or 0.0) / 100.0 * days / 365.0
        elif c.value is not None:
            amount = c.value
        elif p.kind is BondKind.FLOATING:
            amount = face_settle * _coupon_rate(p, start, c.end, curve_date, curves) / 100.0 * days / 365.0
        else:
            rate = p.fixed_rate_pct if p.fixed_rate_pct is not None else c.rate
            amount = face_settle * (rate or 0.0) / 100.0 * days / 365.0
        return amount * (settle_date - start).days / days
    return 0.0
