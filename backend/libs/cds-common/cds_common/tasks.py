"""Background tasks on top of PostgreSQL (no extra broker).

* ``tasks.task_types``  — registry, filled by workers on start-up;
* ``tasks.task_runs``   — every run: params, status, result, error, timings;
* ``tasks.schedules``   — cron schedules, editable by admins (see admin service scheduler).

A worker of service X polls runs with ``queue = X`` using
``SELECT ... FOR UPDATE SKIP LOCKED`` — safe with any number of worker replicas.
"""
from __future__ import annotations

import logging
import signal
import socket
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from .auth import Principal
from .errors import AppError
from .logging import task_id_var

log = logging.getLogger(__name__)

metadata = MetaData(schema="tasks")

task_types = Table(
    "task_types",
    metadata,
    Column("task_type", Text, primary_key=True),
    Column("queue", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("default_params", JSONB, nullable=False, server_default="{}"),
    Column("registered_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

task_runs = Table(
    "task_runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("task_type", Text, nullable=False),
    Column("queue", Text, nullable=False),
    Column("params", JSONB, nullable=False, server_default="{}"),
    Column("status", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("schedule_id", UUID(as_uuid=True)),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("worker", Text),
    Column("result", JSONB),
    Column("error", Text),
)

schedules = Table(
    "schedules",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("task_type", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("cron", Text, nullable=False),
    Column("timezone", Text, nullable=False),
    Column("params", JSONB, nullable=False, server_default="{}"),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("next_run_at", DateTime(timezone=True)),
    Column("last_run_at", DateTime(timezone=True)),
    Column("last_run_id", UUID(as_uuid=True)),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_by", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Trigger(str, Enum):
    USER = "user"
    SCHEDULE = "schedule"
    SYSTEM = "system"


def enqueue(
    session: Session,
    task_type: str,
    params: dict[str, Any] | None = None,
    *,
    trigger: Trigger,
    created_by: str,
    schedule_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a queued run. Caller commits (so the run appears atomically with its cause)."""
    queue = session.execute(select(task_types.c.queue).where(task_types.c.task_type == task_type)).scalar()
    if queue is None:
        raise AppError(f"Неизвестный тип задачи: {task_type}", code="unknown_task_type")
    run_id = uuid.uuid4()
    session.execute(
        insert(task_runs).values(
            id=run_id,
            task_type=task_type,
            queue=queue,
            params=params or {},
            status=TaskStatus.QUEUED.value,
            trigger=trigger.value,
            schedule_id=schedule_id,
            created_by=created_by,
        )
    )
    return run_id


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------
@dataclass
class TaskContext:
    run_id: uuid.UUID
    params: dict[str, Any]
    created_by: str
    trigger: str
    session_factory: sessionmaker
    log: logging.Logger

    @property
    def actor(self) -> Principal:
        """Principal to stamp on audit records produced by this run."""
        if self.trigger == Trigger.USER.value:
            return Principal(sub=f"user:{self.created_by}", username=self.created_by)
        return Principal(sub="system", username=f"system ({self.trigger})")


TaskHandler = Callable[[TaskContext], dict[str, Any] | None]


@dataclass
class TaskDefinition:
    name: str
    title: str
    handler: TaskHandler
    description: str = ""
    default_params: dict[str, Any] = field(default_factory=dict)
    default_schedule: str | None = None  # cron, seeded once when the type first appears


class TaskRegistry:
    def __init__(self, queue: str):
        self.queue = queue
        self.definitions: dict[str, TaskDefinition] = {}

    def task(
        self,
        name: str,
        *,
        title: str,
        description: str = "",
        default_params: dict[str, Any] | None = None,
        default_schedule: str | None = None,
    ) -> Callable[[TaskHandler], TaskHandler]:
        def decorator(func: TaskHandler) -> TaskHandler:
            self.definitions[name] = TaskDefinition(
                name, title, func, description, default_params or {}, default_schedule
            )
            return func

        return decorator


class Worker:
    def __init__(
        self,
        registry: TaskRegistry,
        session_factory: sessionmaker,
        *,
        schedule_timezone: str,
        poll_interval: float = 1.0,
        heartbeat_interval: float = 10.0,
    ):
        self.registry = registry
        self.session_factory = session_factory
        self.schedule_timezone = schedule_timezone
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.name = f"{registry.queue}@{socket.gethostname()}"
        self._stop = threading.Event()
        self._current: uuid.UUID | None = None

    # -- registration -------------------------------------------------------
    def register(self) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as s, s.begin():
            for d in self.registry.definitions.values():
                inserted = s.execute(
                    pg_insert(task_types)
                    .values(
                        task_type=d.name,
                        queue=self.registry.queue,
                        title=d.title,
                        description=d.description,
                        default_params=d.default_params,
                    )
                    .on_conflict_do_nothing()
                    .returning(task_types.c.task_type)
                ).scalar()
                if not inserted:
                    s.execute(
                        update(task_types)
                        .where(task_types.c.task_type == d.name)
                        .values(
                            queue=self.registry.queue,
                            title=d.title,
                            description=d.description,
                            default_params=d.default_params,
                            last_seen_at=now,
                        )
                    )
                elif d.default_schedule:
                    s.execute(
                        insert(schedules).values(
                            id=uuid.uuid4(),
                            task_type=d.name,
                            name=d.title,
                            cron=d.default_schedule,
                            timezone=self.schedule_timezone,
                            params=d.default_params,
                            enabled=True,
                            updated_by="system",
                        )
                    )
                    log.info("Seeded default schedule for %s: %s", d.name, d.default_schedule)
        log.info("Registered task types: %s", ", ".join(self.registry.definitions))

    # -- main loop ------------------------------------------------------------
    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        self.register()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        log.info("Worker %s started", self.name)
        while not self._stop.is_set():
            try:
                claimed = self._claim()
            except Exception:  # DB hiccup: keep the worker alive
                log.exception("Failed to poll task queue")
                claimed = None
            if claimed is None:
                self._stop.wait(self.poll_interval)
                continue
            self._execute(*claimed)
        log.info("Worker %s stopped", self.name)

    def _claim(self) -> tuple[uuid.UUID, str, dict[str, Any], str, str] | None:
        with self.session_factory() as s, s.begin():
            row = s.execute(
                select(task_runs.c.id, task_runs.c.task_type, task_runs.c.params, task_runs.c.created_by, task_runs.c.trigger)
                .where(task_runs.c.queue == self.registry.queue, task_runs.c.status == TaskStatus.QUEUED.value)
                .order_by(task_runs.c.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            s.execute(
                update(task_runs)
                .where(task_runs.c.id == row.id)
                .values(status=TaskStatus.RUNNING.value, started_at=now, heartbeat_at=now, worker=self.name)
            )
            return row.id, row.task_type, row.params or {}, row.created_by, row.trigger

    def _execute(self, run_id: uuid.UUID, task_type: str, params: dict[str, Any], created_by: str, trigger: str) -> None:
        token = task_id_var.set(str(run_id))
        self._current = run_id
        started = time.perf_counter()
        status, result, error = TaskStatus.SUCCEEDED, None, None
        try:
            definition = self.registry.definitions.get(task_type)
            if definition is None:
                raise RuntimeError(f"Worker {self.name} has no handler for {task_type}")
            log.info("Task %s started", task_type, extra={"task_type": task_type})
            ctx = TaskContext(run_id, params, created_by, trigger, self.session_factory, log)
            result = definition.handler(ctx) or {}
        except Exception as exc:  # noqa: BLE001 - a task failure must never kill the worker
            status = TaskStatus.FAILED
            error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
            log.exception("Task %s failed", task_type, extra={"task_type": task_type})
        finally:
            self._current = None
            duration = round(time.perf_counter() - started, 3)
            try:
                with self.session_factory() as s, s.begin():
                    s.execute(
                        update(task_runs)
                        .where(task_runs.c.id == run_id)
                        .values(
                            status=status.value,
                            finished_at=datetime.now(timezone.utc),
                            result=result,
                            error=error,
                        )
                    )
            except Exception:
                log.exception("Failed to store result of task %s", run_id)
            log.info(
                "Task %s %s in %.3fs", task_type, status.value, duration,
                extra={"task_type": task_type, "duration_s": duration},
            )
            task_id_var.reset(token)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            run_id = self._current
            if run_id is None:
                continue
            try:
                with self.session_factory() as s, s.begin():
                    s.execute(
                        update(task_runs)
                        .where(task_runs.c.id == run_id, task_runs.c.status == TaskStatus.RUNNING.value)
                        .values(heartbeat_at=datetime.now(timezone.utc))
                    )
            except Exception:
                log.exception("Heartbeat failed")
