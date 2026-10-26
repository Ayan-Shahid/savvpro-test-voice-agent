"""Alembic runs asynchronously on asyncpg as astro_owner and only ever sees the astro schema."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from astro_api.db import metadata
from astro_api.settings import get_settings

config = context.config
sys.path.insert(0, str(Path(__file__).parent))


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    return name == "astro" if type_ == "schema" else True


def do_run_migrations(connection: Connection) -> None:
    connection.execute(
        text("select set_config('lock_timeout', '5s', false), set_config('statement_timeout', '60s', false)")
    )
    connection.execute(text("set search_path = astro, public"))
    context.configure(
        connection=connection,
        target_metadata=metadata,
        include_schemas=True,
        include_name=include_name,
        version_table_schema="astro",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = config.attributes.get("url") or get_settings().migration_dsn
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported; run against a database")
asyncio.run(run_async_migrations())
