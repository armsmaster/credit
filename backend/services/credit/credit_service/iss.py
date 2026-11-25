"""Client of the Moscow Exchange Informational & Statistical Server (iss.moex.com).

Free ISS data are delayed by 15 minutes. The server resets connections of aggressive clients,
so requests are throttled (ISS_REQUESTS_PER_SECOND) and retried with back-off.
Outbound HTTP goes through HTTP(S)_PROXY from the environment (corporate contour).

TLS: iss.moex.com uses a certificate of Russian Trusted Sub CA, whose root is not in certifi. The client
trusts certifi + the bundled Russian Trusted CA (certs/russian_trusted_ca.crt) + an optional extra CA file
(ISS_CA_BUNDLE, e.g. the CA of a TLS-intercepting corporate proxy). This trust applies to ISS requests only.
"""
from __future__ import annotations

import logging
import ssl
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import certifi
import httpx

from cds_common.errors import UpstreamError

log = logging.getLogger(__name__)

DESCRIPTION_FIELDS = (
    "SECID", "NAME", "SHORTNAME", "ISSUENAME", "ISIN", "REGNUMBER", "ISSUEDATE", "MATDATE", "STARTDATEMOEX",
    "INITIALFACEVALUE", "FACEVALUE", "FACEUNIT", "ISSUESIZE", "LISTLEVEL", "ISQUALIFIEDINVESTORS",
    "COUPONFREQUENCY", "COUPONPERCENT", "COUPONVALUE", "COUPONDATE", "TYPE", "TYPENAME", "SECSUBTYPE",
    "BOND_TYPE", "BOND_SUBTYPE", "COUPON_DETAILS", "COUPON_BENCHMARK", "COUPON_BENCHMARK_SPREAD", "FACEVALUE_TYPE",
    "AMORTBOND", "SUBORDBOND", "STRUCTBOND", "EARLYREPAYMENT", "HASDEFAULT", "HASTECHNICALDEFAULT",
    "OFFERDATE", "PUTOPTIONDATE", "CALLOPTIONDATE", "EMITTER_ID",
)

MARKET_SECURITY_COLUMNS = ("SECID", "FACEVALUE", "ACCRUEDINT", "SETTLEDATE", "FACEVALUEONSETTLEDATE", "PREVWAPRICE",
                           "PREVPRICE", "PREVDATE", "OFFERDATE", "PUTOPTIONDATE", "CALLOPTIONDATE", "STATUS")
MARKET_DATA_COLUMNS = ("SECID", "BID", "OFFER", "LAST", "WAPRICE", "NUMTRADES", "VALTODAY", "UPDATETIME", "SYSTIME",
                       "TRADINGSTATUS", "LCURRENTPRICE")


RUSSIAN_TRUSTED_CA = Path(__file__).parent / "certs" / "russian_trusted_ca.crt"


class IssError(UpstreamError):
    code = "iss_unavailable"


def iss_ssl_context(extra_ca_file: str | None = None) -> ssl.SSLContext:
    """certifi + Russian Trusted Root/Sub CA + an optional extra CA file (added, not replacing)."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(RUSSIAN_TRUSTED_CA))
    if extra_ca_file:
        ctx.load_verify_locations(cafile=extra_ca_file)
    return ctx


def _rows(block: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not block:
        return []
    cols = block.get("columns", [])
    return [dict(zip(cols, r)) for r in block.get("data", [])]


class IssClient:
    def __init__(self, base_url: str, *, requests_per_second: float = 4.0, timeout: float = 30.0, ca_bundle: str | None = None):
        self._http = httpx.Client(base_url=base_url.rstrip("/") + "/", timeout=timeout, verify=iss_ssl_context(ca_bundle), trust_env=True,
                                  headers={"User-Agent": "cds-credit/1.0"})
        self._interval = 1.0 / max(requests_per_second, 0.1)
        self._lock = threading.Lock()
        self._last = 0.0

    def close(self) -> None:
        self._http.close()

    # -- plumbing ----------------------------------------------------------------------
    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + self._interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        params = {"iss.meta": "off", **{k: v for k, v in params.items() if v is not None}}
        last_exc: Exception | None = None
        for attempt in range(5):
            self._throttle()
            try:
                resp = self._http.get(path.lstrip("/"), params=params)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return resp.json()
                last_exc = IssError(f"ISS {resp.status_code}")
            except httpx.HTTPStatusError as exc:
                raise IssError(f"ISS отклонил запрос {path}: {exc.response.status_code}") from None
            except (httpx.TransportError, ValueError) as exc:
                last_exc = exc
            time.sleep(min(2.0 * (attempt + 1), 10.0))
        log.warning("ISS request %s failed: %s", path, last_exc)
        raise IssError(f"Мосбиржа (ISS) недоступна: {last_exc}")

    # -- issuers ---------------------------------------------------------------------
    def search_emitters(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return _rows(self.get("emitters.json", q=query, limit=limit).get("emitters"))

    def emitter(self, emitter_id: int) -> dict[str, Any] | None:
        rows = _rows(self.get(f"emitters/{emitter_id}.json").get("emitter"))
        return rows[0] if rows else None

    def emitter_bonds(self, emitter_id: int) -> list[dict[str, Any]]:
        rows = _rows(self.get(f"emitters/{emitter_id}/securities.json").get("securities"))
        return [r for r in rows if r.get("MARKET") == "bonds"]

    # -- bond static ---------------------------------------------------------------------
    def description(self, secid: str) -> dict[str, Any]:
        rows = _rows(self.get(f"securities/{secid}.json", **{"iss.only": "description"}).get("description"))
        values = {r["name"]: r["value"] for r in rows}
        return {k: values[k] for k in DESCRIPTION_FIELDS if k in values}

    def bondization(self, secid: str) -> dict[str, Any]:
        data = self.get(f"statistics/engines/stock/markets/bonds/bondization/{secid}.json", limit="unlimited")
        return {
            "coupons": [
                {"date": r.get("coupondate"), "start": r.get("startdate"), "record_date": r.get("recorddate"),
                 "value": r.get("value"), "valueprc": r.get("valueprc"), "facevalue": r.get("facevalue")}
                for r in _rows(data.get("coupons"))
            ],
            "amortizations": [
                {"date": r.get("amortdate"), "value": r.get("value"), "valueprc": r.get("valueprc"),
                 "facevalue": r.get("facevalue"), "source": r.get("data_source")}
                for r in _rows(data.get("amortizations"))
            ],
            "offers": [
                {"date": r.get("offerdate"), "price": r.get("price"), "type": r.get("offertype")}
                for r in _rows(data.get("offers"))
            ],
        }

    # -- market data ---------------------------------------------------------------------
    def history(self, secid: str, date_from: date, date_to: date | None = None) -> list[dict[str, Any]]:
        cols = "TRADEDATE,BOARDID,TRADINGSESSION,NUMTRADES,VALUE,CLOSE,WAPRICE,ACCINT,FACEVALUE"
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            data = self.get(
                f"history/engines/stock/markets/bonds/securities/{secid}.json",
                **{"from": date_from.isoformat(), "till": date_to.isoformat() if date_to else None, "start": start,
                   "history.columns": cols},
            )
            rows = _rows(data.get("history"))
            out.extend(rows)
            cursor = _rows(data.get("history.cursor"))
            if not rows or not cursor:
                break
            c = cursor[0]
            start = c["INDEX"] + c["PAGESIZE"]
            if start >= c["TOTAL"]:
                break
        return out

    def board_market(self, board: str) -> dict[str, dict[str, Any]]:
        """Current market data of every bond on a board (one request): secid -> merged row."""
        data = self.get(
            f"engines/stock/markets/bonds/boards/{board}/securities.json",
            **{"iss.only": "securities,marketdata", "securities.columns": ",".join(MARKET_SECURITY_COLUMNS),
               "marketdata.columns": ",".join(MARKET_DATA_COLUMNS)},
        )
        out = {r["SECID"]: dict(r) for r in _rows(data.get("securities"))}
        for r in _rows(data.get("marketdata")):
            out.setdefault(r["SECID"], {}).update(r)
        return out

    def candles(self, board: str, secid: str, date_from: date, interval: int = 60) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            rows = _rows(self.get(
                f"engines/stock/markets/bonds/boards/{board}/securities/{secid}/candles.json",
                **{"from": date_from.isoformat(), "interval": interval, "start": start},
            ).get("candles"))
            out.extend(rows)
            if len(rows) < 500:
                break
            start += len(rows)
        return out
