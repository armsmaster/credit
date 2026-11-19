"""Bond static data: classification, eligibility for a senior credit curve, completeness.

Input is what the ISS (iss.moex.com) tells about a bond — the "description" block and the
bondization schedule (coupons, amortizations, offers) — plus manual data entered by an operator.
Manual values always win. Classification is computed on read, so improving the rules here
re-classifies already stored bonds without a migration.

Supported for credit curves (senior debt only):
  * fixed coupon (known or unknown future coupons, zero-coupon/discount bonds);
  * floating coupon on the key rate (ISS benchmark RREFKEYR) or RUONIA, margin from ISS;
  * put offer: priced to the nearest offer date (redemption at the offer price);
  * amortizing (ISS amortization schedule);
  * indexed notional (VEB.RF linkers): notional indexed daily by compounded RUONIA.
Not supported: subordinated, structured, non-RUB, defaulted, issuer calls, floaters on other
benchmarks (e.g. the OFZ zero curve) — shown with the reason, an operator may override.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

VEB_INN = "7750004150"  # VEB.RF: linkers indexed by RUONIA


class BondKind(str, Enum):
    FIXED = "fixed"
    FLOATING = "floating"
    LINKER = "linker"


class RateIndex(str, Enum):
    KEYRATE = "KEYRATE"
    RUONIA = "RUONIA"


KIND_TITLES = {BondKind.FIXED: "фиксированный купон", BondKind.FLOATING: "плавающий купон", BondKind.LINKER: "индексируемый номинал"}
INDEX_TITLES = {RateIndex.KEYRATE: "ключевая ставка", RateIndex.RUONIA: "RUONIA"}

def benchmark_index(code: str) -> RateIndex | None:
    """ISS coupon benchmark -> supported index: RREFKEYR (key rate), RUONIA / RUONIA_IND (compounded RUONIA)."""
    code = code.strip().upper()
    if code.startswith("RUONIA"):
        return RateIndex.RUONIA
    if code in ("RREFKEYR", "KEYRATE") or code.startswith("RREFKEY"):
        return RateIndex.KEYRATE
    return None

_COUPON_DETAILS = {"ключевая ставка": RateIndex.KEYRATE, "ставка ruonia": RateIndex.RUONIA}

# Manual fields an operator may set (see BondManual in schemas.py).
MANUAL_FIELDS = (
    "kind", "coupon_index", "spread_pct", "lag_days", "fixed_rate_pct", "notional_index",
    "offer_date", "offer_price", "ignore_offer", "force_eligible", "excluded", "dema_half_life_hours", "comment",
)


def _f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _d(value: Any) -> date | None:
    if not value or str(value).startswith("0000"):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _flag(desc: dict[str, Any], key: str) -> bool:
    return str(desc.get(key) or "0") == "1"


@dataclass(frozen=True)
class CouponRow:
    start: date | None
    end: date  # payment date
    record: date | None
    value: float | None  # RUB per bond if known (ISS)
    rate: float | None  # % p.a. if known (ISS valueprc)


@dataclass(frozen=True)
class AmortRow:
    date: date
    value: float | None  # RUB per bond
    percent: float | None  # % of the initial face value
    is_maturity: bool


@dataclass
class BondProfile:
    """Everything needed to decide on and price a bond (ISS data merged with manual data)."""

    secid: str
    kind: BondKind | None
    coupon_index: RateIndex | None = None
    spread_pct: float | None = None
    lag_days: int = 0
    notional_index: RateIndex | None = None
    fixed_rate_pct: float | None = None
    mat_date: date | None = None
    offer_date: date | None = None  # effective put offer (None = priced to maturity)
    offer_price: float = 100.0
    call_date: date | None = None
    initial_face: float | None = None
    coupons: list[CouponRow] = field(default_factory=list)
    amortizations: list[AmortRow] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    ineligible: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return not self.ineligible

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def effective_maturity(self) -> date | None:
        if self.offer_date and (self.mat_date is None or self.offer_date < self.mat_date):
            return self.offer_date
        return self.mat_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value if self.kind else None,
            "coupon_index": self.coupon_index.value if self.coupon_index else None,
            "spread_pct": self.spread_pct,
            "lag_days": self.lag_days,
            "notional_index": self.notional_index.value if self.notional_index else None,
            "fixed_rate_pct": self.fixed_rate_pct,
            "mat_date": self.mat_date,
            "offer_date": self.offer_date,
            "offer_price": self.offer_price,
            "call_date": self.call_date,
            "effective_maturity": self.effective_maturity,
            "features": self.features,
            "eligible": self.eligible,
            "ineligible": self.ineligible,
            "complete": self.complete,
            "missing": self.missing,
            "warnings": self.warnings,
        }


def parse_schedule(schedule: dict[str, Any] | None) -> tuple[list[CouponRow], list[AmortRow], list[tuple[date, float | None, str]]]:
    schedule = schedule or {}
    coupons = []
    for c in schedule.get("coupons") or []:
        end = _d(c.get("date"))
        if end:
            coupons.append(CouponRow(_d(c.get("start")), end, _d(c.get("record_date")), _f(c.get("value")), _f(c.get("valueprc"))))
    amorts = []
    for a in schedule.get("amortizations") or []:
        d = _d(a.get("date"))
        if d:
            amorts.append(AmortRow(d, _f(a.get("value")), _f(a.get("valueprc")), a.get("source") == "maturity"))
    offers = []
    for o in schedule.get("offers") or []:
        d = _d(o.get("date"))
        kind = str(o.get("type") or "")
        if d and "погашение" not in kind.lower():
            offers.append((d, _f(o.get("price")), kind))
    coupons.sort(key=lambda c: c.end)
    amorts.sort(key=lambda a: a.date)
    offers.sort(key=lambda o: o[0])
    return coupons, amorts, offers


def classify(
    secid: str,
    desc: dict[str, Any],
    schedule: dict[str, Any] | None,
    manual: dict[str, Any] | None,
    *,
    today: date,
    issuer_inn: str | None = None,
    is_traded: bool = True,
) -> BondProfile:
    manual = {k: v for k, v in (manual or {}).items() if v not in (None, "")}
    coupons, amorts, offers = parse_schedule(schedule)
    bond_type = str(desc.get("BOND_TYPE") or "")
    bond_subtype = str(desc.get("BOND_SUBTYPE") or "")
    details = str(desc.get("COUPON_DETAILS") or "").strip().lower()
    benchmark = str(desc.get("COUPON_BENCHMARK") or "").strip().upper()
    names = " ".join(str(desc.get(k) or "") for k in ("ISSUENAME", "NAME", "SECSUBTYPE")).lower()

    p = BondProfile(secid=secid, kind=None, coupons=coupons, amortizations=amorts)
    p.mat_date = _d(desc.get("MATDATE"))
    p.initial_face = _f(desc.get("INITIALFACEVALUE")) or _f(desc.get("FACEVALUE"))
    p.lag_days = int(manual.get("lag_days") or 0)

    # ---- kind
    is_linker = "индекс" in str(desc.get("FACEVALUE_TYPE") or "").lower() or "линкер" in bond_type.lower()
    is_floating = "плавающ" in bond_type.lower() or bool(benchmark) or details in _COUPON_DETAILS
    if manual.get("kind"):
        p.kind = BondKind(manual["kind"])
    elif is_linker:
        p.kind = BondKind.LINKER
    elif is_floating:
        p.kind = BondKind.FLOATING
    else:
        p.kind = BondKind.FIXED
    p.features.append(p.kind.value)

    # ---- eligibility (senior RUB debt of supported types)
    face_unit = str(desc.get("FACEUNIT") or "SUR").upper()
    if face_unit not in ("SUR", "RUB", "RUR"):
        p.ineligible.append(f"Валюта номинала {face_unit}")
    if _flag(desc, "STRUCTBOND") or "структурн" in bond_type.lower():
        p.ineligible.append("Структурная облигация")
    if _flag(desc, "SUBORDBOND") or "суборд" in names:
        p.ineligible.append("Субординированная облигация")
    if _flag(desc, "HASDEFAULT"):
        p.ineligible.append("Дефолт по выпуску")
    if _flag(desc, "HASTECHNICALDEFAULT"):
        p.ineligible.append("Технический дефолт")
    if not is_traded:
        p.ineligible.append("Не торгуется")
    if p.mat_date and p.mat_date <= today:
        p.ineligible.append("Погашена")

    call = _d(desc.get("CALLOPTIONDATE"))
    if call and call > today:
        p.call_date = call
    if p.call_date or "call" in bond_subtype.lower():
        p.features.append("call")
        p.ineligible.append("Колл-опцион эмитента" + (f" ({p.call_date.isoformat()})" if p.call_date else ""))

    if _flag(desc, "AMORTBOND") or sum(1 for a in amorts if not a.is_maturity) > 0:
        p.features.append("amortizing")
    if _flag(desc, "EARLYREPAYMENT"):
        p.warnings.append("Условиями допускается досрочное погашение")
    if _flag(desc, "ISQUALIFIEDINVESTORS"):
        p.features.append("qualified")

    # ---- put offer: nearest future offer (manual date wins, can be switched off)
    if not manual.get("ignore_offer"):
        if manual.get("offer_date"):
            p.offer_date = _d(manual["offer_date"])
            p.offer_price = _f(manual.get("offer_price")) or 100.0
        else:
            candidates = [(d, price) for d, price, _ in offers if d > today]
            put = _d(desc.get("PUTOPTIONDATE")) or (_d(desc.get("OFFERDATE")) if "put" in bond_subtype.lower() else None)
            if put and put > today:
                candidates.append((put, None))
            if candidates:
                d, price = min(candidates, key=lambda c: c[0])
                p.offer_date, p.offer_price = d, price or _f(manual.get("offer_price")) or 100.0
    if p.offer_date and p.mat_date and p.offer_date >= p.mat_date:
        p.offer_date = None
    if p.offer_date:
        p.features.append("put")
    elif "put" in bond_subtype.lower():
        p.missing.append("Дата оферты (put) не найдена в ISS")

    # ---- kind-specific data
    if p.kind is BondKind.FLOATING:
        idx = manual.get("coupon_index")
        if idx:
            p.coupon_index = RateIndex(idx)
        elif benchmark:
            p.coupon_index = benchmark_index(benchmark)
            if p.coupon_index is None:
                p.ineligible.append(f"Плавающий купон на {benchmark} не поддерживается")
        else:
            p.coupon_index = _COUPON_DETAILS.get(details)
        p.spread_pct = _f(manual.get("spread_pct"))
        if p.spread_pct is None:
            p.spread_pct = _f(desc.get("COUPON_BENCHMARK_SPREAD"))
        if p.coupon_index is None and not any("не поддерживается" in r for r in p.ineligible):
            p.missing.append("Индекс плавающего купона (ключевая ставка / RUONIA)")
        if p.spread_pct is None:
            p.missing.append("Маржа плавающего купона к индексу")
    elif p.kind is BondKind.LINKER:
        idx = manual.get("notional_index") or (RateIndex.RUONIA.value if issuer_inn == VEB_INN else None)
        p.notional_index = RateIndex(idx) if idx else None
        if p.notional_index is None:
            p.missing.append("Индекс номинала (поддерживается RUONIA)")
        elif p.notional_index is not RateIndex.RUONIA:
            p.ineligible.append("Индексация номинала поддерживается только по RUONIA")
        if "amortizing" in p.features:
            p.ineligible.append("Амортизируемый линкер не поддерживается")
        p.fixed_rate_pct = _f(manual.get("fixed_rate_pct"))
        if p.fixed_rate_pct is None and coupons:
            if not any(c.rate is not None for c in coupons) and _f(desc.get("COUPONPERCENT")) is None:
                p.missing.append("Ставка купона линкера")
    else:
        p.fixed_rate_pct = _f(manual.get("fixed_rate_pct"))

    # ---- completeness of the schedule up to the effective maturity
    eff = p.effective_maturity
    if p.mat_date is None:
        p.missing.append("Дата погашения")
    if not coupons and "дисконт" not in bond_type.lower() and p.kind is not BondKind.LINKER:
        p.missing.append("График купонов (нет данных в ISS)")
    if p.kind is BondKind.FIXED and eff and p.fixed_rate_pct is None:
        unknown = [c for c in coupons if today < c.end <= eff and c.value is None and c.rate is None]
        if unknown:
            p.missing.append(f"Ставка будущих купонов ({len(unknown)} шт. неизвестны в ISS)")
    if p.initial_face is None:
        p.missing.append("Номинал")

    if manual.get("excluded"):
        p.ineligible.insert(0, "Исключена оператором")
    elif manual.get("force_eligible"):
        p.ineligible = [r for r in p.ineligible if r in ("Погашена",)]
        p.warnings.append("Допущена оператором вопреки автоматической проверке")
    return p
