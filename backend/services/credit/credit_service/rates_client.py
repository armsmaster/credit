"""Interest-rate curves from the curves service (internal network, with the caller's token).

The credit service never reads the curves schema directly: it asks the curves API for the curve
in force on the credit curve date (state or latest history version on or before that date).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from cds_common.errors import AppError, UpstreamError

from .domain.rates import RateCurve


class RatesClient:
    def __init__(self, base_url: str, token: str):
        # Internal call: never through the corporate HTTP proxy, whatever NO_PROXY says.
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=15.0, trust_env=False,
                                  headers={"Authorization": f"Bearer {token}"})
        self._cache: dict[tuple[str, date], tuple[RateCurve, dict[str, Any]]] = {}

    def close(self) -> None:
        self._http.close()

    def curve(self, code: str, as_of: date) -> tuple[RateCurve, dict[str, Any]]:
        key = (code, as_of)
        if key in self._cache:
            return self._cache[key]
        try:
            resp = self._http.get(f"/curves/{code}/effective", params={"date": as_of.isoformat()})
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Сервис процентных кривых недоступен: {exc}") from None
        if resp.status_code == 404:
            raise AppError(f"Процентная кривая {code}: нет данных на {as_of} или раньше", code="rate_curve_missing")
        if resp.status_code >= 400:
            raise UpstreamError(f"Сервис процентных кривых ответил {resp.status_code} для {code}")
        snap = resp.json()
        curve_date = date.fromisoformat(snap["as_of_date"])
        nodes = snap["result"]["nodes"]
        curve = RateCurve.from_snapshot(code, curve_date, snap.get("conventions", {}).get("day_count", "ACT/365F"), nodes)
        ref = {"code": code, "as_of_date": snap["as_of_date"], "kind": snap["kind"], "version": snap.get("version")}
        self._cache[key] = (curve, ref)
        return curve, ref
