"""Schema-level guarantees: RLS everywhere, the expected policies exist, and downgrade/upgrade round-trips."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .conftest import ROOT, _psql


def test_every_astro_table_has_forced_rls_and_a_policy(pg: dict[str, Any]) -> None:
    rows = _psql(
        pg["super"],
        "-c",
        "select c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "(select count(*) from pg_policies p where p.schemaname = 'astro' and p.tablename = c.relname) "
        "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'astro' and c.relkind = 'r' and c.relname <> 'alembic_version' order by 1",
    ).splitlines()
    assert len(rows) >= 20
    bad = [r for r in rows if not r.split("|")[1] == r.split("|")[2] == "t" or int(r.split("|")[3]) < 2]
    assert bad == []


def test_no_application_role_bypasses_rls(pg: dict[str, Any]) -> None:
    out = _psql(
        pg["super"],
        "-c",
        "select rolname from pg_roles where rolname in ('astro_api', 'astro_worker', 'astro_owner', "
        "'authenticated', 'anon') and (rolsuper or rolbypassrls)",
    )
    assert out.strip() == ""


def test_definer_functions_pin_search_path_and_are_not_public(pg: dict[str, Any]) -> None:
    out = _psql(
        pg["super"],
        "-c",
        "select p.proname, coalesce(array_to_string(p.proconfig, ','), ''), "
        "has_function_privilege('public', p.oid, 'execute') "
        "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'astro' and p.prosecdef",
    ).splitlines()
    assert len(out) >= 6
    for line in out:
        name, config, public = line.split("|")
        assert 'search_path=""' in config or "search_path=" in config, name
        assert public == "f", name


def test_downgrade_to_base_and_upgrade_again(pg: dict[str, Any]) -> None:
    env = {
        "PATH": "/usr/bin:/bin",
        "ASTRO_DB_HOST": pg["host"],
        "ASTRO_DB_PORT": str(pg["port"]),
        "ASTRO_DB_NAME": pg["name"],
        "PYTHONPATH": str(ROOT / "src"),
        "ASTRO_DB_OWNER_PASSWORD": os.environ.get("ASTRO_TEST_ROLE_PASSWORD", ""),
    }
    alembic = str(Path(ROOT) / ".venv/bin/alembic")
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        res = subprocess.run(
            [alembic, "-c", str(ROOT / "alembic.ini"), *args],
            env=env,
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        )
        assert res.returncode == 0, res.stderr[-2000:]
