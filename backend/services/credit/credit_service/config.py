from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from cds_common.settings import CommonSettings


class CreditSettings(CommonSettings):
    service_name: str = "credit"
    api_prefix: str = "/api/credit"

    # Moscow Exchange ISS. Outbound internet goes through HTTP(S)_PROXY if it is set.
    # TLS trust for ISS = certifi + bundled Russian Trusted CA (iss.moex.com uses it) + ISS_CA_BUNDLE.
    # Behind a TLS-intercepting proxy: point ISS_CA_BUNDLE at the corporate CA (mounted file; it is
    # added to the trust, not replacing it) or use the plain-http endpoint http://iss.moex.com/iss.
    iss_base_url: str = "https://iss.moex.com/iss"
    iss_ca_bundle: str | None = None
    iss_requests_per_second: float = 4.0
    iss_timeout_seconds: float = 30.0
    iss_delay_minutes: int = 15  # free ISS market data are delayed; 0 with a real-time subscription

    # Interest-rate curves service (internal network, called with the user's token).
    curves_api_url: str = "http://curves:8000/api/curves"

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz())

    def today(self) -> date:
        return self.now().date()


@lru_cache
def get_settings() -> CreditSettings:
    return CreditSettings()
