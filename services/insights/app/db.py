"""A small read-only connection pool to the analytics schema."""

from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

# Same libpq settings as services/orchestrator/app/tenants.py: a peer that vanished
# without closing (node lost, cable pulled) must fail the query, not hang it.
# statement_timeout is enforced by the server, which is exactly what is gone.
# Unacked writes give up after 5 s, an idle socket is probed after 10 s.
DEAD_PEER_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 10,
    "keepalives_interval": 3,
    "keepalives_count": 3,
    "tcp_user_timeout": 5000,
}


class Database:
    """Opened without waiting: the service starts (and answers /healthz) whether or
    not Postgres is up yet; /readyz tells Kubernetes when it can take traffic."""

    def __init__(self, dsn: str, *, timeout_s: float = 3.0, max_size: int = 4):
        try:
            conninfo_to_dict(dsn)
        except psycopg.Error:
            # `from None`: libpq's message can quote the string, password included.
            raise ValueError("ANALYTICS_DATABASE_URL is not a valid Postgres DSN") from None
        statement_ms = str(int(timeout_s * 1000))

        async def configure(conn: psycopg.AsyncConnection[Any]) -> None:
            # Postgres bounds every query too, so a slow one ends on the server
            # instead of holding a connection after the caller gave up. Read-only
            # on top of the role's grants: this service never writes.
            await conn.execute("SELECT set_config('statement_timeout', %s, false)", (statement_ms,))
            await conn.execute("SET default_transaction_read_only = on")

        self._pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            dsn,
            open=False,
            name="insights",
            min_size=1,
            max_size=max_size,
            kwargs={
                "autocommit": True,
                "connect_timeout": max(2, round(timeout_s)),  # libpq's minimum is 2 s
                "application_name": "insights",
                **DEAD_PEER_KWARGS,
            },
            configure=configure,
            # A connection broken by a Postgres restart is replaced, not handed out.
            check=AsyncConnectionPool.check_connection,
            timeout=timeout_s,
            reconnect_timeout=10.0,
        )

    async def open(self) -> None:
        await self._pool.open(wait=False)

    async def close(self) -> None:
        await self._pool.close()

    def connection(self) -> Any:
        return self._pool.connection()

    async def ping(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("SELECT 1")
