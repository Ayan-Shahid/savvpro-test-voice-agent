"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text

from .call_service import CallService
from .db import api_database
from .deps import Services, Svc
from .errors import install as install_errors
from .middleware import SecurityMiddleware
from .routes import account, bookings, calls
from .settings import Settings, get_settings
from .snapshot_service import SnapshotService
from .speech.deepgram import Deepgram
from .store import Store

structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])


def create_app(settings: Settings | None = None, *, redis: Redis | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_runtime()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = api_database(settings)
        r = redis or Redis.from_url(
            settings.redis_url, password=settings.redis_password.get_secret_value() or None, decode_responses=False
        )
        store = Store(r)
        snaps = SnapshotService()
        speech = Deepgram(settings)
        app.state.services = Services(settings, db, r, store, snaps, CallService(db, store, snaps, settings), speech)
        try:
            yield
        finally:
            await speech.aclose()
            await db.dispose()
            if redis is None:
                await r.aclose()

    docs = settings.env == "development"
    app = FastAPI(
        title="AstroVoice API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    install_errors(app)
    app.include_router(account.router)
    app.include_router(bookings.router)
    app.include_router(calls.router)

    @app.get("/api/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/ready", tags=["health"])
    async def ready(svc: Svc) -> dict[str, Any]:
        checks: dict[str, str] = {}
        try:
            async with svc.db.as_anon() as conn:
                await conn.execute(text("select 1 from astro.telescopes limit 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "down"
        try:
            await svc.redis.ping()  # type: ignore[misc]
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "down"
        checks["speech"] = "on" if svc.speech.enabled else "text-only"
        ok = checks["database"] == "ok" and checks["redis"] == "ok"
        if not ok:
            from .errors import ApiError

            raise ApiError(503, "not_ready", "The service is starting or under maintenance.", {"checks": checks})
        return {"status": "ok", "checks": checks}

    def _store() -> Store | None:
        svc = getattr(app.state, "services", None)
        return svc.store if svc else None

    app.add_middleware(SecurityMiddleware, settings=settings, store_getter=_store)  # type: ignore[arg-type]
    return app


def app() -> FastAPI:  # uvicorn --factory astro_api.main:app
    return create_app()
