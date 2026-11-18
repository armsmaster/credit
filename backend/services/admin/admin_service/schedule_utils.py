from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from cds_common.errors import AppError


def validate_cron(cron: str, tz: str) -> None:
    if not croniter.is_valid(cron):
        raise AppError(f"Некорректное cron-выражение: {cron!r}", code="invalid_cron")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise AppError(f"Неизвестная временная зона: {tz!r}", code="invalid_timezone") from None


def next_runs(cron: str, tz: str, count: int = 1, after: datetime | None = None) -> list[datetime]:
    """Next fire times (UTC) of a cron expression evaluated in the given timezone."""
    zone = ZoneInfo(tz)
    base = (after or datetime.now(timezone.utc)).astimezone(zone)
    it = croniter(cron, base)
    return [it.get_next(datetime).astimezone(timezone.utc) for _ in range(count)]
