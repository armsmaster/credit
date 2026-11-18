from functools import lru_cache

from pydantic import Field

from cds_common.settings import CommonSettings


class AdminSettings(CommonSettings):
    service_name: str = "admin"
    api_prefix: str = "/api/admin"

    keycloak_client_id: str = "cds-backend"
    keycloak_client_secret: str = ""

    elasticsearch_url: str = "http://elasticsearch:9200"
    logs_index_pattern: str = "cds-logs-*"

    public_url: str = Field("http://localhost:8080", alias="CDS_PUBLIC_URL")
    invite_ttl_days: int = 7

    scheduler_tick_seconds: float = 5.0
    lost_worker_timeout_seconds: int = 120


@lru_cache
def get_settings() -> AdminSettings:
    return AdminSettings()
