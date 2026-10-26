"""Database access. Every request runs in one transaction that first adopts the caller's identity:

    select set_config('request.jwt.claims', <verified claims>, true); set local role authenticated;

Both are transaction-local, so a pooled connection never carries one user into the next request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import orjson
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from .settings import Settings, get_settings

NAMING = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}
metadata = MetaData(schema="astro", naming_convention=NAMING)


class Database:
    def __init__(self, dsn: str, *, pool_size: int = 10, application_name: str = "astrovoice") -> None:
        self.engine: AsyncEngine = create_async_engine(
            dsn,
            pool_size=pool_size,
            max_overflow=pool_size,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={"server_settings": {"application_name": application_name}},
        )

    @asynccontextmanager
    async def as_user(self, claims: dict[str, Any]) -> AsyncIterator[AsyncConnection]:
        """A transaction acting as the signed-in user; RLS policies see `astro.uid()` = claims['sub']."""
        async with self.engine.begin() as conn:
            await conn.execute(
                text("select set_config('request.jwt.claims', :c, true)"), {"c": orjson.dumps(claims).decode()}
            )
            await conn.execute(text("set local role authenticated"))
            yield conn

    @asynccontextmanager
    async def as_anon(self) -> AsyncIterator[AsyncConnection]:
        async with self.engine.begin() as conn:
            await conn.execute(text("set local role anon"))
            yield conn

    @asynccontextmanager
    async def as_service(self) -> AsyncIterator[AsyncConnection]:
        """Worker connections log in as astro_worker and need no role switch."""
        async with self.engine.begin() as conn:
            yield conn

    async def dispose(self) -> None:
        await self.engine.dispose()


def api_database(settings: Settings | None = None) -> Database:
    s = settings or get_settings()
    return Database(s.api_dsn, pool_size=s.db_pool_size, application_name="astro-api")


def worker_database(settings: Settings | None = None) -> Database:
    s = settings or get_settings()
    return Database(s.worker_dsn, pool_size=4, application_name="astro-worker")
