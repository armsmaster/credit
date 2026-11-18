from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from cds_common.settings import CommonSettings


class CurvesSettings(CommonSettings):
    service_name: str = "curves"
    api_prefix: str = "/api/curves"

    def today(self) -> date:
        """Business date in the configured timezone."""
        return datetime.now(ZoneInfo(self.timezone)).date()


@lru_cache
def get_settings() -> CurvesSettings:
    return CurvesSettings()
