"""Read side of the audit journal (all services write into audit.events)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from cds_common.audit import TAG_BACKDATED, audit_events as ev
from cds_common.auth import Principal, require_operator
from cds_common.db import get_session

router = APIRouter(tags=["audit"])


class AuditEventOut(BaseModel):
    id: int
    occurred_at: datetime
    service: str
    entity_type: str
    entity_id: str
    action: str
    actor_username: str
    summary: str
    changes: dict[str, Any] | None
    tags: list[str]
    request_id: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    total: int


@router.get("/audit", response_model=AuditPage)
def list_events(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    service: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    tag: str | None = None,
    q: str | None = Query(None, description="Поиск по описанию и изменениям"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    _: Principal = Depends(require_operator),
):
    conds = []
    if date_from:
        conds.append(ev.c.occurred_at >= date_from)
    if date_to:
        conds.append(ev.c.occurred_at <= date_to)
    for col, value in ((ev.c.service, service), (ev.c.entity_type, entity_type), (ev.c.entity_id, entity_id),
                       (ev.c.action, action), (ev.c.actor_username, actor)):
        if value:
            conds.append(col == value)
    if tag:
        conds.append(ev.c.tags.any(tag))
    if q:
        like = f"%{q}%"
        conds.append(or_(ev.c.summary.ilike(like), cast(ev.c.changes, Text).ilike(like), ev.c.entity_id.ilike(like)))
    total = session.scalar(select(func.count()).select_from(ev).where(*conds))
    rows = session.execute(
        select(ev).where(*conds).order_by(ev.c.occurred_at.desc(), ev.c.id.desc()).limit(limit).offset(offset)
    ).mappings()
    return AuditPage(items=[AuditEventOut(**r) for r in rows], total=total or 0)


@router.get("/audit/facets")
def facets(session: Session = Depends(get_session), _: Principal = Depends(require_operator)) -> dict[str, Any]:
    def distinct(col):
        return [v for (v,) in session.execute(select(col).distinct().order_by(col))]

    tags = [t for (t,) in session.execute(select(func.unnest(ev.c.tags)).distinct())]
    return {
        "services": distinct(ev.c.service),
        "entity_types": distinct(ev.c.entity_type),
        "actions": distinct(ev.c.action),
        "tags": sorted(set(tags) | {TAG_BACKDATED}),
    }
