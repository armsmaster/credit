"""Audit trail of data changes.

Every service writes into ``audit.events`` inside the SAME transaction as the
change itself, so the journal can never disagree with the data.
The table is created by the admin service migrations; services only INSERT.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Column,
    DateTime,
    Identity,
    MetaData,
    Table,
    Text,
    func,
    insert,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from .auth import Principal
from .logging import request_id_var

# Tag for changes of historical data ("изменение задним числом").
TAG_BACKDATED = "backdated"

metadata = MetaData(schema="audit")

audit_events = Table(
    "events",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("service", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("actor_id", Text, nullable=False),
    Column("actor_username", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("changes", JSONB),
    Column("tags", ARRAY(Text), nullable=False, server_default="{}"),
    Column("request_id", Text),
    # Services hold INSERT only (no SELECT): no "RETURNING id" on insert.
    implicit_returning=False,
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def diff(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """{field: {"old": ..., "new": ...}} for fields that differ."""
    old, new = _jsonable(old or {}), _jsonable(new or {})
    return {
        k: {"old": old.get(k), "new": new.get(k)}
        for k in sorted(set(old) | set(new))
        if old.get(k) != new.get(k)
    }


class AuditLog:
    def __init__(self, service: str):
        self.service = service

    def record(
        self,
        session: Session,
        *,
        actor: Principal,
        entity_type: str,
        entity_id: str,
        action: str,
        summary: str,
        changes: dict[str, Any] | None = None,
        tags: Iterable[str] = (),
    ) -> None:
        session.execute(
            insert(audit_events).values(
                service=self.service,
                entity_type=entity_type,
                entity_id=str(entity_id),
                action=action,
                actor_id=actor.sub,
                actor_username=actor.username,
                summary=summary,
                changes=_jsonable(changes) if changes is not None else None,
                tags=list(tags),
                request_id=request_id_var.get(),
            )
        )
