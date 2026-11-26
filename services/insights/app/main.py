"""Insights API: each portal tenant's own call analytics, read from Postgres.

    uvicorn --factory app.main:app_from_env --host 0.0.0.0 --port 8000

The data comes from the `analytics` schema (published by the data pipeline). This
service only reads it, as a role that can do nothing else.
"""

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from app import queries
from app.auth import PLATFORM_ADMIN, Caller, bearer_token, usable_public_key, verify
from app.config import Settings
from app.db import Database
from app.queries import Scope

logger = logging.getLogger("insights")

Clock = Callable[[], date]

# What a data tenant id can look like (tenants/*.yaml ids, or a portal UUID).
# Anything else names no tenant and is refused before any query.
_TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


class Synthetic(StrEnum):
    include = "include"  # simulator and real calls
    exclude = "exclude"  # real calls only
    only = "only"  # simulator calls only


_SYNTHETIC = {Synthetic.include: None, Synthetic.exclude: False, Synthetic.only: True}


def utc_today() -> date:
    return datetime.now(UTC).date()


def create_app(settings: Settings, *, clock: Clock = utc_today) -> FastAPI:
    if not usable_public_key(settings.jwt_public_key):
        # Not fatal (the probes still answer), but every API call will be a 401.
        logger.error("JWT_PUBLIC_KEY is missing or not an RSA public key; refusing all requests.")
    db = Database(
        settings.database_url, timeout_s=settings.query_timeout_s, max_size=settings.pool_max_size
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await db.open()
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(
        title="Insights API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/insights/docs",
        openapi_url="/api/insights/openapi.json",
        redoc_url=None,
    )

    # ── errors ───────────────────────────────────────────────────────────────────

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(psycopg.Error)
    async def database_unavailable(_: Request, exc: Exception) -> JSONResponse:
        # Never echo the driver's message: it can name hosts, roles or SQL.
        logger.warning("analytics query failed: %s", type(exc).__name__)
        return JSONResponse(
            {"detail": "Analytics are unavailable, try again shortly."}, status_code=503
        )

    # ── dependencies ─────────────────────────────────────────────────────────────

    def caller(authorization: Annotated[str | None, Header()] = None) -> Caller:
        return verify(
            bearer_token(authorization),
            public_key=settings.jwt_public_key,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )

    async def scope(
        who: Annotated[Caller, Depends(caller)],
        days: Annotated[int, Query(ge=1, le=365)] = 30,
        synthetic: Synthetic = Synthetic.include,
        tenant: Annotated[str | None, Query(max_length=64)] = None,
    ) -> Scope:
        if who.role == PLATFORM_ADMIN:
            # A platform admin has no tenant of its own: it must say which one.
            if not tenant or not _TENANT_ID.match(tenant):
                raise HTTPException(400, "Pass ?tenant=<data tenant id>.")
            tenants = [tenant]
        else:
            # A tenant admin's tenant comes from its token only; ?tenant is ignored.
            assert who.tenant_id is not None
            own = str(who.tenant_id)
            async with db.connection() as conn:
                linked = await queries.linked_tenants(conn, own)
            tenants = [own, *(t for t in linked if t != own)]
        end = clock()
        return Scope(tenants, end - timedelta(days=days - 1), end, _SYNTHETIC[synthetic])

    Scoped = Annotated[Scope, Depends(scope)]

    async def read(fn: Callable[..., Awaitable[dict[str, Any]]], *args: Any) -> dict[str, Any]:
        async with db.connection() as conn:
            return await fn(conn, *args)

    # ── probes ───────────────────────────────────────────────────────────────────

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        try:
            await db.ping()
        except (psycopg.Error, PoolTimeout):
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    # ── analytics ────────────────────────────────────────────────────────────────

    @app.get("/api/insights/summary")
    async def summary(s: Scoped) -> dict[str, Any]:
        async with db.connection() as conn:
            current = await queries.kpis(conn, s)
            previous = await queries.kpis(conn, s.previous())
            daily = await queries.daily_calls(conn, s)
            published = await queries.updated_at(conn)
        return {
            "tenants": s.tenants,
            "range": {"from": s.start.isoformat(), "to": s.end.isoformat(), "days": s.days},
            "kpis": current,
            "previous": previous,
            "daily": daily,
            "updated_at": published,
        }

    @app.get("/api/insights/sentiment")
    async def sentiment(s: Scoped) -> dict[str, Any]:
        return await read(queries.sentiment, s)

    @app.get("/api/insights/tools")
    async def tools(s: Scoped) -> dict[str, Any]:
        return await read(queries.tools, s)

    @app.get("/api/insights/knowledge")
    async def knowledge(s: Scoped) -> dict[str, Any]:
        return await read(queries.knowledge, s)

    @app.get("/api/insights/activity")
    async def activity(s: Scoped) -> dict[str, Any]:
        return await read(queries.activity, s)

    @app.get("/api/insights/calls")
    async def calls(s: Scoped, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> dict[str, Any]:
        return await read(queries.calls_to_review, s, limit)

    return app


def app_from_env() -> FastAPI:
    """Entry point for `uvicorn --factory`: importing the module (tests) needs no env."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    return create_app(Settings.from_env())
