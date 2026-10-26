"""Run a multi-statement SQL script inside the current migration transaction.

asyncpg prepares every statement, and a prepared statement holds exactly one command, so `op.execute` cannot
take a script. The raw asyncpg connection's `execute()` uses the simple query protocol, which can.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.util import await_


def run(sql: str) -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    await_(raw.driver_connection.execute(sql))  # type: ignore[union-attr]
