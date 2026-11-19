from cds_common.web import create_app

from . import audit_api, auth_api, logs_api, me_api, tasks_api, users
from .config import get_settings
from .keycloak import KeycloakAdmin, KeycloakAuth

settings = get_settings()
app = create_app(
    settings,
    [
        auth_api.router,
        me_api.router,
        users.router,
        users.public_router,
        audit_api.router,
        tasks_api.router,
        logs_api.router,
    ],
    title="CDS — администрирование",
)
app.state.keycloak = KeycloakAdmin(
    settings.keycloak_internal_url, settings.keycloak_realm, settings.keycloak_client_id, settings.keycloak_client_secret
)
app.state.keycloak_auth = KeycloakAuth(settings.keycloak_internal_url, settings.keycloak_realm, settings.keycloak_web_client_id)
