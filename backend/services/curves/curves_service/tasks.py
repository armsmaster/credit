"""Background tasks of the curves service (executed by curves_service.worker)."""
from __future__ import annotations

from datetime import date, timedelta

from cds_common.tasks import TaskContext, TaskRegistry

from .application import CurvesApp
from .config import get_settings

registry = TaskRegistry(queue="curves")


@registry.task(
    "curves.archive_states",
    title="Перенос текущих кривых в историю",
    description=(
        "Копирует текущие состояния кривых в историческое хранилище (версия 1). "
        "Параметры: up_to_date — переносить состояния с датой не позже указанной "
        "(по умолчанию вчера); curve_code — только одна кривая."
    ),
    default_params={},
    default_schedule="30 0 * * *",  # 00:30 every night, business timezone
)
def archive_states(ctx: TaskContext) -> dict:
    settings = get_settings()
    raw = ctx.params.get("up_to_date")
    up_to = date.fromisoformat(raw) if raw else settings.today() - timedelta(days=1)
    with ctx.session_factory() as session:
        result = CurvesApp(session, settings).archive_states(up_to, ctx.actor, ctx.params.get("curve_code"))
    ctx.log.info("Archived %d curve(s), skipped %d", len(result["archived"]), len(result["skipped"]))
    return result
