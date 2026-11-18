from cds_common.web import create_app

from . import audit_api, logs_api, me_api, tasks_api, users
from .config import get_settings
from .keycloak import KeycloakAdmin

settings = get_settings()
app = create_app(
    settings,
    [me_api.router, users.router, users.public_router, audit_api.router, tasks_api.router, logs_api.router],
    title="CDS — администрирование",
)
app.state.keycloak = KeycloakAdmin(
    settings.keycloak_internal_url, settings.keycloak_realm, settings.keycloak_client_id, settings.keycloak_client_secret
)
