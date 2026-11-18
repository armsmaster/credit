"""System logs viewer backed by Elasticsearch (indices cds-logs-YYYY.MM.DD, 7-day ILM retention)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from cds_common.auth import Principal, require_admin
from cds_common.errors import UpstreamError

router = APIRouter(tags=["logs"])


class LogEntry(BaseModel):
    id: str
    timestamp: datetime
    service: str | None
    level: str | None
    logger: str | None
    message: str | None
    request_id: str | None = None
    user: str | None = None
    task_id: str | None = None
    exc_info: str | None = None
    extra: dict[str, Any]


class LogPage(BaseModel):
    items: list[LogEntry]
    total: int


_MAIN_FIELDS = {"@timestamp", "service", "level", "logger", "message", "request_id", "user", "task_id", "exc_info"}


def _search(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    settings = request.app.state.settings
    url = f"{settings.elasticsearch_url}/{settings.logs_index_pattern}/_search"
    try:
        resp = httpx.post(url, json=body, params={"ignore_unavailable": "true", "allow_no_indices": "true"}, timeout=15)
    except httpx.HTTPError as exc:
        raise UpstreamError(f"Elasticsearch недоступен: {exc}") from None
    if resp.status_code >= 300:
        raise UpstreamError(f"Ошибка Elasticsearch: {resp.status_code} {resp.text[:300]}")
    return resp.json()


@router.get("/logs", response_model=LogPage)
def search_logs(
    request: Request,
    q: str | None = Query(None, description="Полнотекстовый поиск (синтаксис simple_query_string)"),
    service: list[str] | None = Query(None),
    level: list[str] | None = Query(None),
    request_id: str | None = None,
    task_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    size: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=9000),
    _: Principal = Depends(require_admin),
):
    now = datetime.now(timezone.utc)
    filters: list[dict[str, Any]] = [
        {"range": {"@timestamp": {"gte": (date_from or now - timedelta(hours=1)).isoformat(),
                                  "lte": (date_to or now).isoformat()}}}
    ]
    if service:
        filters.append({"terms": {"service": service}})
    if level:
        filters.append({"terms": {"level": [lv.upper() for lv in level]}})
    if request_id:
        filters.append({"term": {"request_id": request_id}})
    if task_id:
        filters.append({"term": {"task_id": task_id}})
    must = [{"simple_query_string": {"query": q, "fields": ["message", "exc_info"], "default_operator": "and"}}] if q else []
    data = _search(
        request,
        {
            "query": {"bool": {"filter": filters, "must": must}},
            "sort": [{"@timestamp": {"order": "desc"}}],
            "from": offset,
            "size": size,
            "track_total_hits": True,
        },
    )
    items = []
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        msg = src.get("message")
        items.append(
            LogEntry(
                id=hit["_id"],
                timestamp=src.get("@timestamp"),
                service=src.get("service"),
                level=src.get("level"),
                logger=src.get("logger") or src.get("loggerName"),
                message=msg if isinstance(msg, str) else (None if msg is None else str(msg)),
                request_id=src.get("request_id"),
                user=src.get("user"),
                task_id=src.get("task_id"),
                exc_info=src.get("exc_info"),
                extra={k: v for k, v in src.items() if k not in _MAIN_FIELDS},
            )
        )
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    return LogPage(items=items, total=total)


@router.get("/logs/services")
def log_services(request: Request, _: Principal = Depends(require_admin)) -> dict[str, list[str]]:
    data = _search(
        request,
        {
            "size": 0,
            "query": {"range": {"@timestamp": {"gte": "now-7d"}}},
            "aggs": {"services": {"terms": {"field": "service", "size": 100}}, "levels": {"terms": {"field": "level", "size": 20}}},
        },
    )
    aggs = data.get("aggregations", {})
    return {
        "services": sorted(b["key"] for b in aggs.get("services", {}).get("buckets", [])),
        "levels": sorted(b["key"] for b in aggs.get("levels", {}).get("buckets", [])),
    }
