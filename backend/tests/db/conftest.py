"""Builds a throwaway database the same way a fresh deploy does: roles and schemas, the slice of `auth`
GoTrue would create, the bootstrap grants, then `alembic upgrade head` as astro_owner.

Needs a Postgres reachable through ASTRO_TEST_PG (a superuser libpq URL); the tests skip without it.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text

from astro_api.db import Database

ROOT = Path(__file__).parents[2]
SQL = ROOT / "sql"
SUPER = os.environ.get("ASTRO_TEST_PG", "")
# In CI the roles already exist (created by infra/postgres/initdb.sh) with this password; locally trust auth.
ROLE_PW = os.environ.get("ASTRO_TEST_ROLE_PASSWORD", "")
pytestmark = pytest.mark.db


def _psql(url: str, *args: str, input_sql: str | None = None) -> str:
    res = subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-qAt", *args],
        input=input_sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode:
        raise RuntimeError(res.stderr)
    return res.stdout


def _url(base: str, db: str, user: str | None = None) -> str:
    from urllib.parse import urlsplit, urlunsplit

    u = urlsplit(base)
    netloc = u.netloc if user is None else f"{user}@{u.hostname}:{u.port or 5432}"
    return urlunsplit((u.scheme, netloc, f"/{db}", u.query, ""))


@pytest.fixture(scope="session")
def pg() -> Iterator[dict[str, Any]]:
    if not SUPER:
        pytest.skip("ASTRO_TEST_PG not set")
    name = f"astro_test_{uuid.uuid4().hex[:8]}"
    admin = _url(SUPER, "postgres")
    _psql(admin, "-c", f"create database {name}")
    db = _url(SUPER, name)
    roles_exist = _psql(admin, "-c", "select count(*) from pg_roles where rolname = 'astro_owner'").strip() == "1"
    if roles_exist:  # roles are cluster-wide: reuse them, but recreate this database's schemas
        script = (SQL / "00_roles_and_schemas.sql").read_text().split("create schema auth", 1)[1]
        _psql(db, "-v", f"dbname={name}", input_sql="create schema auth" + script)
    else:
        _psql(
            db,
            "-v",
            f"dbname={name}",
            "-v",
            "auth_admin_password=test",
            "-v",
            "owner_password=test",
            "-v",
            "api_password=test",
            "-v",
            "worker_password=test",
            "-f",
            str(SQL / "00_roles_and_schemas.sql"),
        )
    if int(_psql(db, "-c", "show server_version_num").strip()) < 180000:
        _psql(db, "-f", str(SQL / "test_uuidv7_shim.sql"))
    _psql(db, "-c", "set role supabase_auth_admin", "-f", str(SQL / "test_auth_stub.sql"))
    _psql(db, "-f", str(SQL / "10_bootstrap.sql"))
    host_db = _url(SUPER, name)
    from urllib.parse import urlsplit

    u = urlsplit(host_db)
    env = {
        **os.environ,
        "ASTRO_DB_HOST": u.hostname or "localhost",
        "ASTRO_DB_PORT": str(u.port or 5432),
        "ASTRO_DB_NAME": name,
        "PYTHONPATH": str(ROOT / "src"),
        "ASTRO_DB_OWNER_PASSWORD": ROLE_PW,
    }
    res = subprocess.run(
        [str(ROOT / ".venv/bin/alembic"), "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if res.returncode:
        _psql(admin, "-c", f"drop database {name} with (force)")
        raise RuntimeError(res.stderr[-3000:])
    yield {"name": name, "super": db, "host": env["ASTRO_DB_HOST"], "port": env["ASTRO_DB_PORT"]}
    _psql(admin, "-c", f"drop database {name} with (force)")


def _dsn(pg: dict[str, Any], user: str) -> str:
    auth = f"{user}:{ROLE_PW}" if ROLE_PW else user
    return f"postgresql+asyncpg://{auth}@{pg['host']}:{pg['port']}/{pg['name']}"


@pytest_asyncio.fixture(loop_scope="session")
async def api_db(pg: dict[str, Any]) -> AsyncIterator[Database]:
    db = Database(_dsn(pg, "astro_api"), pool_size=2)
    yield db
    await db.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def worker_db(pg: dict[str, Any]) -> AsyncIterator[Database]:
    db = Database(_dsn(pg, "astro_worker"), pool_size=2)
    yield db
    await db.dispose()


def make_user(pg: dict[str, Any], email: str, name: str = "Test User", app_meta: str = "{}") -> str:
    uid = str(uuid.uuid4())
    _psql(
        pg["super"],
        "-c",
        f"insert into auth.users (id, email, raw_user_meta_data, raw_app_meta_data) values "
        f"('{uid}', '{email}', jsonb_build_object('display_name', '{name}'), '{app_meta}')",
    )
    return uid


def claims(uid: str, *, admin: bool = False) -> dict[str, Any]:
    c: dict[str, Any] = {"sub": uid, "role": "authenticated", "aal": "aal2" if admin else "aal1"}
    if admin:
        c["app_metadata"] = {"role": "admin"}
    return c


async def scalar(db: Database, c: dict[str, Any] | None, sql: str, **params: Any) -> Any:
    ctx = db.as_user(c) if c else db.as_anon()
    async with ctx as conn:
        return (await conn.execute(text(sql), params)).scalar()


ANCHOR = __import__("datetime").date(2026, 10, 27)


@pytest_asyncio.fixture(scope="session")
async def seeded(pg: dict[str, Any]) -> dict[str, Any]:
    """Seed the demo world (10 nights of sky) with users inserted straight into the auth stub."""
    from astro_api.settings import Settings
    from astro_seed.__main__ import run

    people: dict[str, str] = {}

    async def create(email: str, password: str, name: str, grade: str) -> str:
        uid = make_user(pg, email, name)
        people[email.split("@")[0]] = uid
        return uid

    report = await run(
        Settings(env="test"),
        anchor=ANCHOR,
        nights=10,
        catalogue_only=False,
        reset=False,
        create_user=create,
        dsn=_dsn(pg, "astro_owner"),
    )
    return {"report": report, "people": people}
