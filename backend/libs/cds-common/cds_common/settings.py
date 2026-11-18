from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    """Settings shared by every service. Values come from environment variables."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    service_name: str = "service"
    api_prefix: str = "/api/service"
    database_url: str = "postgresql+psycopg://localhost/cds"
    log_level: str = "INFO"
    timezone: str = Field("Europe/Moscow", alias="CDS_TIMEZONE")

    # Tokens are issued for the public URL, keys are fetched over the internal network.
    keycloak_issuer: str = "http://localhost:8080/auth/realms/cds"
    keycloak_internal_url: str = "http://keycloak:8080/auth"
    keycloak_realm: str = "cds"
    keycloak_audience: str = "cds-api"

    @property
    def jwks_url(self) -> str:
        return f"{self.keycloak_internal_url}/realms/{self.keycloak_realm}/protocol/openid-connect/certs"
