"""Keycloak clients used by the admin service.

* KeycloakAuth  — user sessions for our own login page (password / refresh grants of the
  public client cds-web, called over the internal network; the browser never talks to Keycloak);
* KeycloakAdmin — Admin REST API with the service account of the cds-backend client.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from cds_common.errors import AppError, Conflict, NotFound, UpstreamError

log = logging.getLogger(__name__)


class AuthError(AppError):
    status_code = 401
    code = "auth_failed"

    def __init__(self, detail: str, *, code: str, status_code: int):
        super().__init__(detail, code=code)
        self.status_code = status_code


# Keycloak error_description -> (code, HTTP status, message for the user)
_GRANT_ERRORS = {
    "Invalid user credentials": ("invalid_credentials", 401, "Неверный логин или пароль"),
    "Account disabled": ("account_disabled", 403, "Учётная запись заблокирована. Обратитесь к администратору"),
    "Account temporarily disabled": ("account_locked", 423, "Слишком много неудачных попыток входа. Попробуйте позже"),
    # Returned only when the password is correct but a required action is pending (temporary password).
    "Account is not fully set up": ("password_change_required", 409, "Нужно задать новый пароль"),
    # Realm imported before the own login page: keycloak-config has not run (or failed).
    "Client not allowed for direct access grants": (
        "login_not_configured", 503,
        "Вход не настроен в Keycloak: администратору нужно выполнить docker compose up (задача keycloak-config)",
    ),
}


class KeycloakAuth:
    def __init__(self, base_url: str, realm: str, client_id: str):
        oidc = f"{base_url}/realms/{realm}/protocol/openid-connect"
        self._token_url = f"{oidc}/token"
        self._logout_url = f"{oidc}/logout"
        self._client_id = client_id

    def _post(self, url: str, data: dict[str, str]) -> httpx.Response:
        try:
            return httpx.post(url, data={"client_id": self._client_id, **data}, timeout=10.0)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Сервис авторизации недоступен: {exc}") from None

    def password(self, username: str, password: str) -> dict[str, Any]:
        resp = self._post(self._token_url, {"grant_type": "password", "username": username, "password": password, "scope": "openid"})
        if resp.status_code == 200:
            return resp.json()
        description = self._description(resp)
        code, status, message = _GRANT_ERRORS.get(description, ("auth_failed", 502, "Ошибка сервиса авторизации"))
        if status == 502:
            log.error("Keycloak password grant failed: %s %s", resp.status_code, resp.text[:300])
        raise AuthError(message, code=code, status_code=status)

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        resp = self._post(self._token_url, {"grant_type": "refresh_token", "refresh_token": refresh_token})
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code >= 500:
            raise UpstreamError("Сервис авторизации недоступен")
        raise AuthError("Сессия истекла, войдите снова", code="session_expired", status_code=401)

    def logout(self, refresh_token: str) -> None:
        resp = self._post(self._logout_url, {"refresh_token": refresh_token})
        if resp.status_code >= 300:
            log.warning("Keycloak logout returned %s", resp.status_code)

    @staticmethod
    def _description(resp: httpx.Response) -> str:
        try:
            return resp.json().get("error_description", "")
        except ValueError:
            return ""


class KeycloakAdmin:
    def __init__(self, base_url: str, realm: str, client_id: str, client_secret: str):
        self._token_url = f"{base_url}/realms/{realm}/protocol/openid-connect/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(base_url=f"{base_url}/admin/realms/{realm}", timeout=10.0)
        self._token: str | None = None
        self._token_expires = 0.0
        self._lock = threading.Lock()

    # -- plumbing -----------------------------------------------------------
    def _access_token(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or time.monotonic() > self._token_expires:
                try:
                    resp = httpx.post(
                        self._token_url,
                        data={"grant_type": "client_credentials"},
                        auth=(self._client_id, self._client_secret),
                        timeout=10.0,
                    )
                except httpx.HTTPError as exc:
                    raise UpstreamError(f"Keycloak недоступен: {exc}") from None
                if resp.status_code != 200:
                    raise UpstreamError(f"Keycloak отказал в токене сервиса: {resp.status_code} {resp.text[:200]}")
                body = resp.json()
                self._token = body["access_token"]
                self._token_expires = time.monotonic() + body.get("expires_in", 60) - 15
            return self._token  # type: ignore[return-value]

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._access_token(force=attempt == 2)}"}
            try:
                resp = self._http.request(method, path, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise UpstreamError(f"Keycloak недоступен: {exc}") from None
            if resp.status_code != 401:
                break
        if resp.status_code == 404:
            raise NotFound("Пользователь не найден в Keycloak")
        if resp.status_code == 409:
            raise Conflict(self._error_message(resp) or "Пользователь с таким логином или email уже существует")
        if resp.status_code == 400:
            raise AppError(self._error_message(resp) or "Keycloak отклонил запрос", code="keycloak_rejected")
        if resp.status_code >= 300:
            log.error("Keycloak %s %s -> %s %s", method, path, resp.status_code, resp.text[:500])
            raise UpstreamError(f"Ошибка Keycloak: {resp.status_code}")
        return resp

    @staticmethod
    def _error_message(resp: httpx.Response) -> str | None:
        try:
            body = resp.json()
        except ValueError:
            return None
        return body.get("errorMessage") or body.get("error_description") or body.get("error")

    # -- users --------------------------------------------------------------
    def list_users(self, search: str | None = None, first: int = 0, max_results: int = 500) -> list[dict]:
        params: dict[str, Any] = {"first": first, "max": max_results, "briefRepresentation": "false"}
        if search:
            params["search"] = search
        return self._request("GET", "/users", params=params).json()

    def get_user(self, user_id: str) -> dict:
        return self._request("GET", f"/users/{user_id}").json()

    def find_user(self, username: str) -> dict:
        users = self._request("GET", "/users", params={"username": username, "exact": "true"}).json()
        if not users:
            raise NotFound("Пользователь не найден")
        return users[0]

    def create_user(self, representation: dict) -> str:
        resp = self._request("POST", "/users", json=representation)
        return resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    def update_user(self, user_id: str, representation: dict) -> None:
        self._request("PUT", f"/users/{user_id}", json=representation)

    def logout_user(self, user_id: str) -> None:
        self._request("POST", f"/users/{user_id}/logout")

    def reset_password(self, user_id: str, password: str, temporary: bool = False) -> None:
        self._request(
            "PUT",
            f"/users/{user_id}/reset-password",
            json={"type": "password", "value": password, "temporary": temporary},
        )

    # -- roles --------------------------------------------------------------
    def realm_role(self, name: str) -> dict:
        return self._request("GET", f"/roles/{name}").json()

    def role_members(self, name: str) -> list[dict]:
        return self._request("GET", f"/roles/{name}/users", params={"first": 0, "max": 5000}).json()

    def user_realm_roles(self, user_id: str) -> list[dict]:
        return self._request("GET", f"/users/{user_id}/role-mappings/realm").json()

    def add_realm_roles(self, user_id: str, roles: list[dict]) -> None:
        if roles:
            self._request("POST", f"/users/{user_id}/role-mappings/realm", json=roles)

    def remove_realm_roles(self, user_id: str, roles: list[dict]) -> None:
        if roles:
            self._request("DELETE", f"/users/{user_id}/role-mappings/realm", json=roles)
