"""Keycloak access-token verification and role checks for FastAPI."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .logging import user_var

log = logging.getLogger(__name__)


class Role(str, Enum):
    VIEWER = "cds-viewer"
    OPERATOR = "cds-operator"
    ADMIN = "cds-admin"

    @property
    def title(self) -> str:
        return {Role.VIEWER: "Просмотр", Role.OPERATOR: "Оператор", Role.ADMIN: "Администратор"}[self]


# Admin ⊃ Operator ⊃ Viewer (mirrors Keycloak composite roles).
_IMPLIED = {
    Role.ADMIN: {Role.ADMIN, Role.OPERATOR, Role.VIEWER},
    Role.OPERATOR: {Role.OPERATOR, Role.VIEWER},
    Role.VIEWER: {Role.VIEWER},
}


def effective_roles(raw: list[str] | set[str]) -> frozenset[Role]:
    out: set[Role] = set()
    for r in raw:
        try:
            out |= _IMPLIED[Role(r)]
        except ValueError:
            continue
    return frozenset(out)


@dataclass(frozen=True)
class Principal:
    sub: str
    username: str
    name: str = ""
    email: str = ""
    roles: frozenset[Role] = field(default_factory=frozenset)

    def has(self, role: Role) -> bool:
        return role in self.roles

    @property
    def primary_role(self) -> Role | None:
        for r in (Role.ADMIN, Role.OPERATOR, Role.VIEWER):
            if r in self.roles:
                return r
        return None


SYSTEM = Principal(sub="system", username="system", name="Система")


class TokenVerifier:
    def __init__(self, issuer: str, jwks_url: str, audience: str):
        self.issuer = issuer
        self.audience = audience
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600, timeout=5)

    def verify(self, token: str) -> Principal:
        signing_key = self._jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.issuer,
            leeway=30,
            options={"require": ["exp", "iat", "sub"]},
        )
        return Principal(
            sub=claims["sub"],
            username=claims.get("preferred_username", claims["sub"]),
            name=claims.get("name", ""),
            email=claims.get("email", ""),
            roles=effective_roles(claims.get("realm_access", {}).get("roles", [])),
        )


_bearer = HTTPBearer(auto_error=False)


def get_principal(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется авторизация")
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        principal = verifier.verify(creds.credentials)
    except jwt.PyJWKClientError as exc:
        log.error("Cannot fetch Keycloak signing keys: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Сервис авторизации недоступен") from None
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Недействительный токен: {exc}") from None
    user_var.set(principal.username)
    request.state.principal = principal
    return principal


def require_role(role: Role):
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Недостаточно прав: нужна роль «{role.title}»")
        return principal

    return dependency


require_viewer = require_role(Role.VIEWER)
require_operator = require_role(Role.OPERATOR)
require_admin = require_role(Role.ADMIN)
