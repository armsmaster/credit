"""Monitoring of background tasks and management of schedules."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from cds_common.audit import AuditLog, diff
from cds_common.auth import Principal, require_admin, require_operator, require_viewer
from cds_common.db import get_session
from cds_common.errors import Conflict, NotFound
from cds_common.tasks import TaskStatus, Trigger, enqueue, schedules, task_runs, task_types

from .schedule_utils import next_runs, validate_cron

router = APIRouter()
audit = AuditLog("admin")


# ---------------------------------------------------------------- schemas
class TaskTypeOut(BaseModel):
    task_type: str
    queue: str
    title: str
    description: str
    default_params: dict[str, Any]
    last_seen_at: datetime


class TaskRunOut(BaseModel):
    id: uuid.UUID
    task_type: str
    queue: str
    params: dict[str, Any]
    status: str
    trigger: str
    schedule_id: uuid.UUID | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    worker: str | None
    duration_s: float | None
    result: dict[str, Any] | list | None = None
    error: str | None = None


class TaskPage(BaseModel):
    items: list[TaskRunOut]
    total: int


class RunTaskIn(BaseModel):
    task_type: str
    params: dict[str, Any] = Field(default_factory=dict)


class ScheduleIn(BaseModel):
    task_type: str
    name: str = Field(min_length=1, max_length=200)
    cron: str = Field(min_length=9, max_length=100, description="cron: минуты часы день месяц день_недели")
    timezone: str = "Europe/Moscow"
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ScheduleOut(ScheduleIn):
    id: uuid.UUID
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_id: uuid.UUID | None
    last_run_status: str | None = None
    updated_by: str
    updated_at: datetime
    version: int


def _run_out(row: Any, full: bool = False) -> TaskRunOut:
    data = dict(row)
    start, end = data.get("started_at"), data.get("finished_at")
    data["duration_s"] = (end - start).total_seconds() if start and end else None
    if not full:
        data["result"] = None
        data["error"] = (data.get("error") or "").split("\n", 1)[0] or None
    return TaskRunOut(**data)


# ---------------------------------------------------------------- task types and runs
@router.get("/task-types", response_model=list[TaskTypeOut], tags=["tasks"])
def list_task_types(session: Session = Depends(get_session), _: Principal = Depends(require_viewer)):
    return [TaskTypeOut(**r) for r in session.execute(select(task_types).order_by(task_types.c.task_type)).mappings()]


@router.get("/tasks", response_model=TaskPage, tags=["tasks"])
def list_runs(
    status: list[str] | None = Query(None),
    task_type: str | None = None,
    trigger: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    _: Principal = Depends(require_viewer),
):
    conds = []
    if status:
        conds.append(task_runs.c.status.in_(status))
    if task_type:
        conds.append(task_runs.c.task_type == task_type)
    if trigger:
        conds.append(task_runs.c.trigger == trigger)
    if date_from:
        conds.append(task_runs.c.created_at >= date_from)
    if date_to:
        conds.append(task_runs.c.created_at <= date_to)
    total = session.scalar(select(func.count()).select_from(task_runs).where(*conds)) or 0
    rows = session.execute(
        select(task_runs).where(*conds).order_by(task_runs.c.created_at.desc()).limit(limit).offset(offset)
    ).mappings()
    return TaskPage(items=[_run_out(r) for r in rows], total=total)


@router.get("/tasks/summary", tags=["tasks"])
def summary(session: Session = Depends(get_session), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    by_status = dict(
        session.execute(
            select(task_runs.c.status, func.count()).where(task_runs.c.created_at >= since).group_by(task_runs.c.status)
        ).all()
    )
    active = session.scalar(
        select(func.count()).select_from(task_runs).where(
            task_runs.c.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value])
        )
    )
    last_by_type = (
        select(task_runs.c.task_type, task_runs.c.status, task_runs.c.created_at, task_runs.c.finished_at)
        .distinct(task_runs.c.task_type)
        .order_by(task_runs.c.task_type, task_runs.c.created_at.desc())
    )
    return {
        "last_24h": {s.value: by_status.get(s.value, 0) for s in TaskStatus},
        "active": active or 0,
        "last_by_type": [dict(r) for r in session.execute(last_by_type).mappings()],
    }


@router.get("/tasks/{run_id}", response_model=TaskRunOut, tags=["tasks"])
def get_run(run_id: uuid.UUID, session: Session = Depends(get_session), _: Principal = Depends(require_viewer)):
    row = session.execute(select(task_runs).where(task_runs.c.id == run_id)).mappings().first()
    if not row:
        raise NotFound("Задача не найдена")
    return _run_out(row, full=True)


@router.post("/tasks", response_model=TaskRunOut, status_code=202, tags=["tasks"])
def run_task(data: RunTaskIn, session: Session = Depends(get_session), user: Principal = Depends(require_operator)):
    run_id = enqueue(session, data.task_type, data.params, trigger=Trigger.USER, created_by=user.username)
    session.commit()
    return get_run(run_id, session, user)


@router.post("/tasks/{run_id}/cancel", response_model=TaskRunOut, tags=["tasks"])
def cancel_run(run_id: uuid.UUID, session: Session = Depends(get_session), user: Principal = Depends(require_operator)):
    res = session.execute(
        update(task_runs)
        .where(task_runs.c.id == run_id, task_runs.c.status == TaskStatus.QUEUED.value)
        .values(status=TaskStatus.CANCELLED.value, finished_at=func.now(), error=f"Отменена пользователем {user.username}")
    )
    if res.rowcount == 0:
        raise Conflict("Отменить можно только задачу в очереди")
    session.commit()
    return get_run(run_id, session, user)


# ---------------------------------------------------------------- schedules (admin)
@router.get("/schedules", response_model=list[ScheduleOut], tags=["schedules"])
def list_schedules(session: Session = Depends(get_session), _: Principal = Depends(require_admin)):
    last_status = select(task_runs.c.status).where(task_runs.c.id == schedules.c.last_run_id).scalar_subquery()
    rows = session.execute(
        select(schedules, last_status.label("last_run_status")).order_by(schedules.c.name)
    ).mappings()
    return [ScheduleOut(**r) for r in rows]


@router.get("/schedules/preview", tags=["schedules"])
def preview(cron: str, timezone_name: str = Query("Europe/Moscow", alias="timezone"), _: Principal = Depends(require_admin)):
    validate_cron(cron, timezone_name)
    return {"next_runs": next_runs(cron, timezone_name, count=5)}


def _check_task_type(session: Session, task_type: str) -> None:
    if not session.scalar(select(func.count()).select_from(task_types).where(task_types.c.task_type == task_type)):
        raise NotFound(f"Тип задачи {task_type} не зарегистрирован")


def _get_schedule(session: Session, schedule_id: uuid.UUID) -> dict[str, Any]:
    row = session.execute(select(schedules).where(schedules.c.id == schedule_id)).mappings().first()
    if not row:
        raise NotFound("Расписание не найдено")
    return dict(row)


@router.post("/schedules", response_model=ScheduleOut, status_code=201, tags=["schedules"])
def create_schedule(data: ScheduleIn, session: Session = Depends(get_session), user: Principal = Depends(require_admin)):
    validate_cron(data.cron, data.timezone)
    _check_task_type(session, data.task_type)
    sid = uuid.uuid4()
    values = data.model_dump()
    session.execute(
        insert(schedules).values(
            id=sid, **values, updated_by=user.username,
            next_run_at=next_runs(data.cron, data.timezone)[0] if data.enabled else None,
        )
    )
    audit.record(
        session, actor=user, entity_type="schedule", entity_id=str(sid), action="create",
        summary=f"Создано расписание «{data.name}» ({data.task_type}, {data.cron})", changes=diff(None, values),
    )
    session.commit()
    return list_schedule_by_id(sid, session)


@router.put("/schedules/{schedule_id}", response_model=ScheduleOut, tags=["schedules"])
def update_schedule(
    schedule_id: uuid.UUID, data: ScheduleIn, session: Session = Depends(get_session), user: Principal = Depends(require_admin)
):
    validate_cron(data.cron, data.timezone)
    _check_task_type(session, data.task_type)
    before = _get_schedule(session, schedule_id)
    values = data.model_dump()
    session.execute(
        update(schedules)
        .where(schedules.c.id == schedule_id)
        .values(
            **values,
            updated_by=user.username,
            updated_at=func.now(),
            version=schedules.c.version + 1,
            next_run_at=next_runs(data.cron, data.timezone)[0] if data.enabled else None,
        )
    )
    changes = diff({k: before[k] for k in values}, values)
    if changes:
        audit.record(
            session, actor=user, entity_type="schedule", entity_id=str(schedule_id), action="update",
            summary=f"Изменено расписание «{data.name}»", changes=changes,
        )
    session.commit()
    return list_schedule_by_id(schedule_id, session)


@router.delete("/schedules/{schedule_id}", status_code=204, tags=["schedules"])
def delete_schedule(schedule_id: uuid.UUID, session: Session = Depends(get_session), user: Principal = Depends(require_admin)):
    before = _get_schedule(session, schedule_id)
    session.execute(delete(schedules).where(schedules.c.id == schedule_id))
    audit.record(
        session, actor=user, entity_type="schedule", entity_id=str(schedule_id), action="delete",
        summary=f"Удалено расписание «{before['name']}»",
        changes=diff({k: before[k] for k in ("task_type", "cron", "timezone", "params", "enabled")}, None),
    )
    session.commit()


@router.post("/schedules/{schedule_id}/run", response_model=TaskRunOut, status_code=202, tags=["schedules"])
def run_schedule_now(schedule_id: uuid.UUID, session: Session = Depends(get_session), user: Principal = Depends(require_admin)):
    sch = _get_schedule(session, schedule_id)
    run_id = enqueue(session, sch["task_type"], sch["params"], trigger=Trigger.USER, created_by=user.username,
                     schedule_id=schedule_id)
    session.commit()
    return get_run(run_id, session, user)


def list_schedule_by_id(schedule_id: uuid.UUID, session: Session) -> ScheduleOut:
    last_status = select(task_runs.c.status).where(task_runs.c.id == schedules.c.last_run_id).scalar_subquery()
    row = session.execute(
        select(schedules, last_status.label("last_run_status")).where(schedules.c.id == schedule_id)
    ).mappings().first()
    if not row:
        raise NotFound("Расписание не найдено")
    return ScheduleOut(**row)


@router.get("/schedules/timezone-default", tags=["schedules"], include_in_schema=False)
def default_timezone(request: Request, _: Principal = Depends(require_admin)):
    return {"timezone": request.app.state.settings.timezone}
