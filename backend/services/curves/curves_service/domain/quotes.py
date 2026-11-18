"""Market quotes of par instruments. All rates are in percent (16.25 means 16.25%)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from .conventions import Tenor


class InputMode(str, Enum):
    BID_ASK = "bid_ask"  # tenor, bid, ask
    MID_SPREAD = "mid_spread"  # tenor, mid, spread (spread = ask - bid, percent)


@dataclass(frozen=True)
class Quote:
    tenor: Tenor
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def half_spread_bp(self) -> float:
        return 50.0 * self.spread


class QuoteError(ValueError):
    pass


def _num(row: dict[str, Any], key: str, idx: int) -> float:
    if key not in row or row[key] is None:
        raise QuoteError(f"Строка {idx + 1}: нет поля '{key}'")
    try:
        return float(row[key])
    except (TypeError, ValueError):
        raise QuoteError(f"Строка {idx + 1}: поле '{key}' должно быть числом") from None


def parse_quotes(rows: Iterable[dict[str, Any]], mode: InputMode) -> list[Quote]:
    """Validate raw JSON rows and normalise them to bid/ask quotes sorted by tenor."""
    quotes: list[Quote] = []
    seen: set[object] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise QuoteError(f"Строка {idx + 1}: ожидается объект")
        try:
            tenor = Tenor.parse(row.get("tenor", ""))
        except ValueError as exc:
            raise QuoteError(f"Строка {idx + 1}: {exc}") from None
        key = tenor.months if tenor.months is not None else str(tenor)  # 1Y == 12M
        if key in seen:
            raise QuoteError(f"Тенор {tenor} указан дважды")
        seen.add(key)
        if mode is InputMode.BID_ASK:
            bid, ask = _num(row, "bid", idx), _num(row, "ask", idx)
        else:
            mid, spread = _num(row, "mid", idx), _num(row, "spread", idx)
            if spread < 0:
                raise QuoteError(f"Строка {idx + 1}: спред не может быть отрицательным")
            bid, ask = mid - spread / 2, mid + spread / 2
        if bid > ask:
            raise QuoteError(f"Строка {idx + 1} ({tenor}): bid больше ask")
        if not (-20.0 < bid < 200.0 and -20.0 < ask < 200.0):
            raise QuoteError(f"Строка {idx + 1} ({tenor}): ставки задаются в процентах")
        quotes.append(Quote(tenor, bid, ask))
    if not quotes:
        raise QuoteError("Нет котировок")
    quotes.sort(key=lambda q: q.tenor.sort_key())
    if quotes[-1].tenor.sort_key() > 50 * 12:
        raise QuoteError("Максимальный тенор — 50Y")
    return quotes
