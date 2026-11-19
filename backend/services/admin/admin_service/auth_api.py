"""Own login API (backend-for-frontend over Keycloak).

The browser never talks to Keycloak: it sends credentials here, the service obtains tokens
from Keycloak over the internal network and
* returns the short-lived access token in the response body (kept in memory by the UI),
* keeps the refresh token in an httpOnly, SameSite=Strict cookie (invisible to JavaScript).
No Web Crypto is needed in the browser, so the UI works over plain HTTP on any host name.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cds_common.audit import AuditLog
from cds_common.auth import Principal
from cds_common.db import get_session
from cds_common.errors import AppError

from .keycloak import AuthError, KeycloakAdmin, KeycloakAuth

log = logging.getLogger("cds.auth")
audit = AuditLog("admin")
router = APIRouter(prefix="/public/auth", tags=["auth"])

COOKIE = "cds_refresh"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class FirstLoginIn(LoginIn):
    new_password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    expires_in: int


def _cookie_path(request: Request) -> str:
    return f"{request.app.state.settings.api_prefix}/public/auth"


def start_session(request: Request, response: Response, tokens: dict[str, Any]) -> TokenOut:
    """Put the refresh token into the session cookie and return the access token."""
    response.set_cookie(
        COOKIE,
        tokens["refresh_token"],
        max_age=int(tokens.get("refresh_expires_in") or 28800),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path=_cookie_path(request),
    )
    return TokenOut(access_token=tokens["access_token"], expires_in=int(tokens.get("expires_in", 300)))


def _end_session(request: Request, response: Response) -> None:
    response.delete_cookie(COOKIE, path=_cookie_path(request), samesite="strict", httponly=True)


def get_auth(request: Request) -> KeycloakAuth:
    return request.app.state.keycloak_auth


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, request: Request, response: Response, auth: KeycloakAuth = Depends(get_auth)):
    try:
        tokens = auth.password(data.username, data.password)
    except AuthError as exc:
        log.warning("Login failed for %s: %s", data.username, exc.code, extra={"auth_user": data.username})
        raise
    log.info("Login: %s", data.username, extra={"auth_user": data.username})
    return start_session(request, response, tokens)


@router.post("/refresh", response_model=TokenOut)
def refresh(
    request: Request,
    response: Response,
    cds_refresh: str | None = Cookie(default=None),
    auth: KeycloakAuth = Depends(get_auth),
):
    if not cds_refresh:
        raise AuthError("Требуется вход", code="not_authenticated", status_code=401)
    try:
        tokens = auth.refresh(cds_refresh)
    except AuthError:
        _end_session(request, response)
        raise
    return start_session(request, response, tokens)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    cds_refresh: str | None = Cookie(default=None),
    auth: KeycloakAuth = Depends(get_auth),
):
    if cds_refresh:
        auth.logout(cds_refresh)
    _end_session(request, response)


@router.post("/first-login", response_model=TokenOut)
def first_login(
    data: FirstLoginIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    auth: KeycloakAuth = Depends(get_auth),
):
    """Replace a temporary password (e.g. of the bootstrap administrator) and log in."""
    if data.new_password == data.password:
        raise AppError("Новый пароль должен отличаться от временного", code="same_password")
    try:
        tokens = auth.password(data.username, data.password)
    except AuthError as exc:
        if exc.code != "password_change_required":
            raise
        # Keycloak reports "not fully set up" only for a CORRECT password: the old one is verified.
    else:
        return start_session(request, response, tokens)

    kc: KeycloakAdmin = request.app.state.keycloak
    user = kc.find_user(data.username)
    kc.reset_password(user["id"], data.new_password, temporary=False)  # password policy errors -> 400
    actions = [a for a in user.get("requiredActions", []) if a != "UPDATE_PASSWORD"]
    kc.update_user(user["id"], {"requiredActions": actions})
    audit.record(
        session, actor=Principal(sub=user["id"], username=user["username"]), entity_type="user",
        entity_id=user["id"], action="password_change", summary=f"Пользователь {user['username']} сменил временный пароль",
    )
    session.commit()
    log.info("Temporary password replaced: %s", data.username, extra={"auth_user": data.username})
    try:
        tokens = auth.password(data.username, data.new_password)
    except AuthError as exc:
        if exc.code == "password_change_required":
            raise AppError("Учётная запись требует настройки администратором", code="account_setup_required") from None
        raise
    return start_session(request, response, tokens)
