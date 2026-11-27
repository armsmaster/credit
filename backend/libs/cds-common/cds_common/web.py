"""FastAPI application factory with the conventions every service shares."""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .auth import TokenVerifier
from .db import Database
from .errors import AppError
from .logging import request_id_var, setup_logging, user_var
from .settings import CommonSettings

log = logging.getLogger("cds.http")


def create_app(settings: CommonSettings, routers: Iterable[APIRouter], *, title: str, version: str = "0.1.0") -> FastAPI:
    setup_logging(settings.service_name, settings.log_level)
    prefix = settings.api_prefix
    app = FastAPI(
        title=title,
        version=version,
        docs_url=f"{prefix}/docs",
        openapi_url=f"{prefix}/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.db = Database(settings.database_url, application_name=settings.service_name)
    app.state.token_verifier = TokenVerifier(settings.keycloak_issuer, settings.jwks_url, settings.keycloak_audience)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        rid_token = request_id_var.set(rid)
        user_token = user_var.set(None)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            path = request.url.path
            if not path.endswith("/health"):
                log.info(
                    "%s %s %s", request.method, path, status,
                    extra={
                        "method": request.method,
                        "path": path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            request_id_var.reset(rid_token)
            user_var.reset(user_token)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception):
        log.exception("Unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера", "code": "internal"})

    health = APIRouter()

    @health.get("/health", include_in_schema=False)
    @health.get(f"{prefix}/health", include_in_schema=False)
    def healthcheck(request: Request):
        with request.app.state.db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "service": settings.service_name}

    app.include_router(health)
    for router in routers:
        app.include_router(router, prefix=prefix)
    return app


def mount_public_api(app: FastAPI, routers: Iterable[APIRouter], *, title: str, description: str, version: str = "1") -> FastAPI:
    """Open read-only API of a service: a separate application under {api_prefix}/public with its own Swagger
    ({api_prefix}/public/docs) that lists only these endpoints. No token is checked — put only read-only,
    publishable data here."""
    from starlette.routing import Mount

    settings: CommonSettings = app.state.settings
    public = FastAPI(title=title, description=description, version=version, docs_url="/docs", openapi_url="/openapi.json",
                     redoc_url=None, swagger_ui_parameters={"defaultModelsExpandDepth": 0, "displayRequestDuration": True})
    public.state.settings = settings
    public.state.db = app.state.db
    for router in routers:
        public.include_router(router)

    @public.exception_handler(AppError)
    async def public_app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})

    # before the service's own routes, so that none of their path parameters can capture /public/...
    app.router.routes.insert(0, Mount(f"{settings.api_prefix}/public", app=public))
    return public
