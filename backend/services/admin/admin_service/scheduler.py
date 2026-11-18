"""Scheduler: turns due cron schedules into queued task runs; fails runs of lost workers.

Run as a separate process: python -m admin_service.scheduler
Safe to run in several replicas (rows are claimed with FOR UPDATE SKIP LOCKED).
"""
from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from cds_common.db import Database
from cds_common.errors import AppError
from cds_common.logging import setup_logging
from cds_common.tasks import TaskStatus, Trigger, enqueue, schedules, task_runs

from .config import get_settings
from .schedule_utils import next_runs

log = logging.getLogger("scheduler")


def tick(db: Database, lost_after: timedelta) -> None:
    now = datetime.now(timezone.utc)
    with db.session() as s, s.begin():
        due = s.execute(
            select(schedules)
            .where(schedules.c.enabled.is_(True), or_(schedules.c.next_run_at.is_(None), schedules.c.next_run_at <= now))
            .with_for_update(skip_locked=True)
        ).mappings().all()
        for sch in due:
            try:
                upcoming = next_runs(sch["cron"], sch["timezone"], after=now)[0]
            except Exception:  # invalid cron stored somehow: disable rather than spin
                log.exception("Invalid schedule %s, disabling", sch["id"])
                s.execute(update(schedules).where(schedules.c.id == sch["id"]).values(enabled=False))
                continue
            if sch["next_run_at"] is None:  # new or re-enabled schedule: just plan it
                s.execute(update(schedules).where(schedules.c.id == sch["id"]).values(next_run_at=upcoming))
                continue
            values: dict = {"next_run_at": upcoming}
            try:
                with s.begin_nested():
                    run_id = enqueue(
                        s, sch["task_type"], sch["params"], trigger=Trigger.SCHEDULE,
                        created_by=f"schedule:{sch['name']}", schedule_id=sch["id"],
                    )
                values.update(last_run_at=now, last_run_id=run_id)
                log.info("Schedule '%s' fired: %s -> run %s", sch["name"], sch["task_type"], run_id)
            except AppError as exc:
                log.error("Schedule '%s' cannot enqueue %s: %s", sch["name"], sch["task_type"], exc.detail)
            s.execute(update(schedules).where(schedules.c.id == sch["id"]).values(**values))

        lost = s.execute(
            update(task_runs)
            .where(task_runs.c.status == TaskStatus.RUNNING.value, task_runs.c.heartbeat_at < now - lost_after)
            .values(status=TaskStatus.FAILED.value, finished_at=now, error="Воркер перестал отвечать (нет heartbeat)")
            .returning(task_runs.c.id)
        ).scalars().all()
        for run_id in lost:
            log.warning("Task run %s marked failed: worker lost", run_id)


def main() -> None:
    settings = get_settings()
    setup_logging("scheduler", settings.log_level)
    db = Database(settings.database_url, application_name="scheduler")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("Scheduler started, tick=%ss", settings.scheduler_tick_seconds)
    lost_after = timedelta(seconds=settings.lost_worker_timeout_seconds)
    while not stop.is_set():
        try:
            tick(db, lost_after)
        except Exception:
            log.exception("Scheduler tick failed")
        stop.wait(settings.scheduler_tick_seconds)
    log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
