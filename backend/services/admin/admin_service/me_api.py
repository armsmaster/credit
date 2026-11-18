"""Current user info and per-user UI preferences (shared by all microfrontends)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from cds_common.auth import Principal, get_principal
from cds_common.db import get_session

from .models import UserPreference

router = APIRouter(tags=["me"])
KEY = Path(pattern=r"^[a-z0-9_.\-]{1,100}$")


@router.get("/me")
def me(user: Principal = Depends(get_principal)) -> dict[str, Any]:
    return {
        "id": user.sub,
        "username": user.username,
        "name": user.name,
        "email": user.email,
        "roles": sorted(r.value for r in user.roles),
        "primary_role": user.primary_role.value if user.primary_role else None,
    }


@router.get("/me/preferences/{key}")
def get_preference(key: str = KEY, session: Session = Depends(get_session), user: Principal = Depends(get_principal)):
    row = session.scalars(select(UserPreference).where(UserPreference.user_id == user.sub, UserPreference.key == key)).first()
    return {"key": key, "value": row.value if row else None}


@router.put("/me/preferences/{key}")
def put_preference(
    key: str = KEY,
    value: Any = Body(..., embed=True),
    session: Session = Depends(get_session),
    user: Principal = Depends(get_principal),
):
    stmt = pg_insert(UserPreference).values(user_id=user.sub, key=key, value=value)
    session.execute(stmt.on_conflict_do_update(index_elements=["user_id", "key"], set_={"value": value}))
    session.commit()
    return {"key": key, "value": value}
