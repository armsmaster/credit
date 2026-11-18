"""User management through Keycloak + one-time invitation links for the first login."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cds_common.audit import AuditLog
from cds_common.auth import Principal, Role, require_admin
from cds_common.db import get_session
from cds_common.errors import AppError, Conflict, NotFound

from .config import AdminSettings
from .keycloak import KeycloakAdmin
from .models import Invitation

audit = AuditLog("admin")
ROLE_PRIORITY = [Role.ADMIN, Role.OPERATOR, Role.VIEWER]


# ---------------------------------------------------------------- schemas
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    role: Role
    invite_ttl_days: int | None = Field(None, ge=1, le=90)


class UserOut(BaseModel):
    id: str
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None
    enabled: bool
    role: Role | None
    created_at: datetime | None
    invite_pending: bool = False
    invite_expires_at: datetime | None = None


class InviteOut(BaseModel):
    link: str
    expires_at: datetime


class UserCreatedOut(BaseModel):
    user: UserOut
    invite: InviteOut


class RoleChange(BaseModel):
    role: Role


class EnabledChange(BaseModel):
    enabled: bool


class InviteRequest(BaseModel):
    ttl_days: int | None = Field(None, ge=1, le=90)


class InvitationInfo(BaseModel):
    username: str
    status: str  # valid | expired | used | revoked
    expires_at: datetime


class AcceptInvite(BaseModel):
    password: str = Field(min_length=8, max_length=128)


# ---------------------------------------------------------------- service
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserService:
    def __init__(self, kc: KeycloakAdmin, session: Session, settings: AdminSettings):
        self.kc = kc
        self.s = session
        self.settings = settings

    def _roles_by_user(self) -> dict[str, Role]:
        out: dict[str, Role] = {}
        for role in reversed(ROLE_PRIORITY):  # higher roles overwrite lower ones
            for member in self.kc.role_members(role.value):
                out[member["id"]] = role
        return out

    def _pending_invites(self) -> dict[str, datetime]:
        rows = self.s.scalars(
            select(Invitation).where(
                Invitation.used_at.is_(None), Invitation.revoked_at.is_(None), Invitation.expires_at > _now()
            )
        )
        return {r.user_id: r.expires_at for r in rows}

    @staticmethod
    def _out(u: dict[str, Any], role: Role | None, invite_exp: datetime | None) -> UserOut:
        ts = u.get("createdTimestamp")
        return UserOut(
            id=u["id"],
            username=u["username"],
            email=u.get("email"),
            first_name=u.get("firstName"),
            last_name=u.get("lastName"),
            enabled=u.get("enabled", False),
            role=role,
            created_at=datetime.fromtimestamp(ts / 1000, timezone.utc) if ts else None,
            invite_pending=invite_exp is not None,
            invite_expires_at=invite_exp,
        )

    def list_users(self, search: str | None) -> list[UserOut]:
        users = [u for u in self.kc.list_users(search) if not u["username"].startswith("service-account-")]
        roles, invites = self._roles_by_user(), self._pending_invites()
        return sorted(
            (self._out(u, roles.get(u["id"]), invites.get(u["id"])) for u in users),
            key=lambda x: x.username,
        )

    def _set_single_role(self, user_id: str, role: Role) -> None:
        current = {r["name"]: r for r in self.kc.user_realm_roles(user_id)}
        to_remove = [current[r.value] for r in Role if r.value in current and r != role]
        self.kc.remove_realm_roles(user_id, to_remove)
        if role.value not in current:
            self.kc.add_realm_roles(user_id, [self.kc.realm_role(role.value)])

    def _issue_invite(self, user_id: str, username: str, actor: Principal, ttl_days: int | None) -> InviteOut:
        self.s.execute(
            update(Invitation)
            .where(Invitation.user_id == user_id, Invitation.used_at.is_(None), Invitation.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        token = secrets.token_urlsafe(32)
        expires = _now() + timedelta(days=ttl_days or self.settings.invite_ttl_days)
        self.s.add(
            Invitation(
                id=uuid.uuid4(), user_id=user_id, username=username, token_hash=_hash(token),
                expires_at=expires, created_by=actor.username,
            )
        )
        audit.record(
            self.s, actor=actor, entity_type="user", entity_id=user_id, action="invite_issue",
            summary=f"Выпущена ссылка первого входа для {username} (до {expires:%Y-%m-%d %H:%M} UTC)",
        )
        return InviteOut(link=f"{self.settings.public_url.rstrip('/')}/invite/{token}", expires_at=expires)

    def create_user(self, data: UserCreate, actor: Principal) -> UserCreatedOut:
        user_id = self.kc.create_user(
            {
                "username": data.username,
                "email": data.email,
                "firstName": data.first_name,
                "lastName": data.last_name,
                "enabled": True,
                "emailVerified": True,
            }
        )
        self.kc.add_realm_roles(user_id, [self.kc.realm_role(data.role.value)])
        audit.record(
            self.s, actor=actor, entity_type="user", entity_id=user_id, action="create",
            summary=f"Создан пользователь {data.username} с ролью «{data.role.title}»",
            changes={k: {"old": None, "new": v} for k, v in data.model_dump(mode="json", exclude={"invite_ttl_days"}).items()},
        )
        invite = self._issue_invite(user_id, data.username, actor, data.invite_ttl_days)
        self.s.commit()
        u = self.kc.get_user(user_id)
        return UserCreatedOut(user=self._out(u, data.role, invite.expires_at), invite=invite)

    def change_role(self, user_id: str, role: Role, actor: Principal) -> UserOut:
        if user_id == actor.sub and role != Role.ADMIN:
            raise Conflict("Нельзя понизить собственную роль")
        u = self.kc.get_user(user_id)
        old = self._roles_by_user().get(user_id)
        self._set_single_role(user_id, role)
        audit.record(
            self.s, actor=actor, entity_type="user", entity_id=user_id, action="role_change",
            summary=f"Роль пользователя {u['username']}: «{old.title if old else '—'}» → «{role.title}»",
            changes={"role": {"old": old.value if old else None, "new": role.value}},
        )
        self.s.commit()
        return self._out(u, role, self._pending_invites().get(user_id))

    def set_enabled(self, user_id: str, enabled: bool, actor: Principal) -> UserOut:
        if user_id == actor.sub and not enabled:
            raise Conflict("Нельзя заблокировать самого себя")
        u = self.kc.get_user(user_id)
        self.kc.update_user(user_id, {"enabled": enabled})
        if not enabled:
            self.kc.logout_user(user_id)
        audit.record(
            self.s, actor=actor, entity_type="user", entity_id=user_id, action="unblock" if enabled else "block",
            summary=f"Пользователь {u['username']} {'разблокирован' if enabled else 'заблокирован'}",
            changes={"enabled": {"old": u.get("enabled"), "new": enabled}},
        )
        self.s.commit()
        u["enabled"] = enabled
        return self._out(u, self._roles_by_user().get(user_id), self._pending_invites().get(user_id))

    def reissue_invite(self, user_id: str, ttl_days: int | None, actor: Principal) -> InviteOut:
        u = self.kc.get_user(user_id)
        invite = self._issue_invite(user_id, u["username"], actor, ttl_days)
        self.s.commit()
        return invite

    # ---- public part (no authentication: the token itself is the credential) ----
    def _find_invite(self, token: str) -> Invitation:
        inv = self.s.scalars(select(Invitation).where(Invitation.token_hash == _hash(token))).first()
        if not inv:
            raise NotFound("Ссылка недействительна")
        return inv

    @staticmethod
    def _status(inv: Invitation) -> str:
        if inv.used_at:
            return "used"
        if inv.revoked_at:
            return "revoked"
        if inv.expires_at <= _now():
            return "expired"
        return "valid"

    def invitation_info(self, token: str) -> InvitationInfo:
        inv = self._find_invite(token)
        return InvitationInfo(username=inv.username, status=self._status(inv), expires_at=inv.expires_at)

    def accept_invite(self, token: str, password: str) -> InvitationInfo:
        inv = self._find_invite(token)
        status = self._status(inv)
        if status != "valid":
            raise AppError(
                {"used": "Ссылка уже использована", "revoked": "Ссылка отозвана", "expired": "Срок действия ссылки истёк"}[status],
                code=f"invite_{status}",
            )
        user = self.kc.get_user(inv.user_id)
        if not user.get("enabled"):
            raise AppError("Учётная запись заблокирована", code="user_disabled")
        self.kc.reset_password(inv.user_id, password, temporary=False)
        inv.used_at = _now()
        audit.record(
            self.s, actor=Principal(sub=inv.user_id, username=inv.username), entity_type="user",
            entity_id=inv.user_id, action="invite_accept", summary=f"Пользователь {inv.username} задал пароль по ссылке",
        )
        self.s.commit()
        return InvitationInfo(username=inv.username, status="used", expires_at=inv.expires_at)


# ---------------------------------------------------------------- routers
router = APIRouter(tags=["users"])
public_router = APIRouter(tags=["invitations"])


def get_service(request: Request, session: Session = Depends(get_session)) -> UserService:
    return UserService(request.app.state.keycloak, session, request.app.state.settings)


@router.get("/users", response_model=list[UserOut])
def list_users(search: str | None = None, svc: UserService = Depends(get_service), _: Principal = Depends(require_admin)):
    return svc.list_users(search)


@router.post("/users", response_model=UserCreatedOut, status_code=201)
def create_user(data: UserCreate, svc: UserService = Depends(get_service), actor: Principal = Depends(require_admin)):
    return svc.create_user(data, actor)


@router.put("/users/{user_id}/role", response_model=UserOut)
def change_role(user_id: str, data: RoleChange, svc: UserService = Depends(get_service), actor: Principal = Depends(require_admin)):
    return svc.change_role(user_id, data.role, actor)


@router.put("/users/{user_id}/enabled", response_model=UserOut)
def set_enabled(user_id: str, data: EnabledChange, svc: UserService = Depends(get_service), actor: Principal = Depends(require_admin)):
    return svc.set_enabled(user_id, data.enabled, actor)


@router.post("/users/{user_id}/invite", response_model=InviteOut)
def reissue_invite(
    user_id: str, data: InviteRequest, svc: UserService = Depends(get_service), actor: Principal = Depends(require_admin)
):
    return svc.reissue_invite(user_id, data.ttl_days, actor)


@public_router.get("/public/invitations/{token}", response_model=InvitationInfo)
def invitation_info(token: str, svc: UserService = Depends(get_service)):
    return svc.invitation_info(token)


@public_router.post("/public/invitations/{token}/accept", response_model=InvitationInfo)
def accept_invite(token: str, data: AcceptInvite, svc: UserService = Depends(get_service)):
    return svc.accept_invite(token, data.password)
