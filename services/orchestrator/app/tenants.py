"""Who a call can be for: the test tenants, and every client signed up on the platform.

Two sources behind one lookup:

  test       tenants/*.yaml, loaded at startup: flows, tools, in-prompt knowledge.
  platform   accounts_tenant in the portal's Postgres, read at call start, so a
             client who signed up a minute ago is callable without a restart. No
             flows and no tools: a generic assistant that answers from the client's
             own documents, retrieved per turn (app/knowledge.py).

The YAML always wins. A test tenant never touches Postgres, and a platform client
whose slug collides with one is left out of the listing (and logged).

Platform answers are cached, 30 s when found and 5 s when not, and served stale
when Postgres fails: a known client stays callable through a database restart.
Every query is bounded (~2 s), because it sits between the caller and the greeting.

The orchestrator reads as voice_reader, a role that may select document_chunks and
four columns of accounts_tenant, so every query on accounts_tenant names exactly
those (id, name, slug, is_active).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

from .flow import Tenant

log = logging.getLogger("tenants")

FOUND_TTL_S = 30.0          # a known client: re-read at most twice a minute
NOT_FOUND_TTL_S = 5.0       # short: a client who just signed up is callable almost at once
LISTING_TTL_S = 5.0         # the picker's list; spares Postgres a query per page load
LOOKUP_TIMEOUT_S = 2.0      # the longest a call start waits on Postgres
# Shorter than the edge's 2 s deadline on ListTenants, so that with Postgres slow
# the picker still gets the test tenants rather than a timeout.
LISTING_TIMEOUT_S = 1.5
# After a failed re-read, the old answer is served without asking again for this
# long. Otherwise, with Postgres down, every call start would first wait out the
# timeout (a refused connection still costs the pool its full wait).
STALE_RETRY_S = 5.0
MAX_CACHED_SLUGS = 1024     # bounds memory when callers probe random slugs

PLATFORM_LOCALE = "en-US"
PLATFORM_SPEAKER = "Aria"

# What a portal slug can be (Django's SlugField). Anything else cannot name a
# client, so it is answered without a query.
_SLUG = re.compile(r"^[-A-Za-z0-9_]{1,64}$")

_TENANT_SQL = "SELECT id, name, slug, is_active FROM accounts_tenant WHERE slug = %s"
_LISTING_SQL = """
SELECT t.id, t.name, t.slug, t.is_active, coalesce(d.documents, 0)
FROM accounts_tenant AS t
LEFT JOIN (
    SELECT tenant_id, count(DISTINCT document_id) AS documents
    FROM document_chunks
    GROUP BY tenant_id
) AS d ON d.tenant_id = t.id
WHERE t.is_active
"""


class DirectoryUnavailable(Exception):
    """Postgres did not answer, and nothing was ever read for this slug."""


@dataclass(frozen=True)
class PlatformTenant:
    """One accounts_tenant row; `documents` is filled in by listings only."""

    id: str
    name: str
    slug: str
    is_active: bool
    documents: int = 0


@dataclass(frozen=True)
class TenantSummary:
    """One ListTenants entry."""

    slug: str
    display_name: str
    locale: str
    source: str  # "test" | "platform"
    documents: int


# TenantDirectory has a list() method, which hides the builtin inside its body.
PlatformRows = list[PlatformTenant]


class PlatformSource(Protocol):
    """Where platform tenants are read from: Postgres, or a fake in tests."""

    async def fetch(self, slug: str) -> PlatformTenant | None: ...

    async def fetch_active(self) -> list[PlatformTenant]: ...


def display_name(row: PlatformTenant) -> str:
    """The client's name on one line. It is user input that lands in the system
    prompt, so it must not be able to start a line of its own."""
    return " ".join(row.name.split()) or row.slug


def platform_tenant(row: PlatformTenant) -> Tenant:
    """A signed-up client as a tenant: a generic assistant for its documents."""
    name = display_name(row)
    return Tenant(
        tenant_id=row.id,
        display_name=name,
        slug=row.slug,
        locale=PLATFORM_LOCALE,
        voice={
            "speaker": PLATFORM_SPEAKER,
            "greeting": f"Hello, you've reached {name}. How can I help you today?",
        },
        persona=(
            f"You are the phone assistant for {name}. You answer callers' questions "
            f"using only {name}'s own documents, which you are given as reference "
            "material.\n"
            "If the documents do not cover a question, say so plainly and offer to "
            f"take a message for someone from {name} to call back.\n"
            "Never invent prices, dates, policies, availability or any other fact "
            "that is not in the documents."
        ),
        endpointing={},
        flows=[],
        tools=[],
        knowledge_text="",
        guardrails=[],
        escalation={
            "message": f"Let me take a message and have someone from {name} call you back."
        },
        source="platform",
        knowledge_id=row.id,
    )


@dataclass
class _Entry:
    """What Postgres last said about one slug. None = no active client by that name."""

    tenant: Tenant | None
    read_at: float
    expires_at: float


@dataclass
class _Listing:
    rows: list[PlatformTenant]
    read_at: float
    expires_at: float


class TenantDirectory:
    """Resolves a slug at call start, and lists what can be called."""

    def __init__(
        self,
        test_tenants: Mapping[str, Tenant],
        platform: PlatformSource | None = None,
        *,
        timeout_s: float = LOOKUP_TIMEOUT_S,
        listing_timeout_s: float = LISTING_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.test_tenants = dict(test_tenants)
        self.platform = platform
        self.timeout_s = timeout_s
        self.listing_timeout_s = listing_timeout_s
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._listing: _Listing | None = None
        # One query per key at a time: a burst of calls for a client whose entry
        # just expired shares one lookup instead of queueing for the pool.
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self._collisions: set[tuple[str, str]] = set()

    async def get(self, slug: str) -> Tenant | None:
        """The tenant to run a call for, or None if no such tenant exists.

        Raises DirectoryUnavailable when Postgres cannot answer and the slug was
        never read before: that is not the same as an unknown slug.
        """
        if (tenant := self.test_tenants.get(slug)) is not None:
            return tenant
        if self.platform is None or not _SLUG.match(slug):
            return None

        entry = self._cache.get(slug)
        if entry is not None and entry.expires_at > self._clock():
            return entry.tenant
        try:
            entry = await self._bounded(f"tenant:{slug}", lambda: self._read_tenant(slug),
                                        self.timeout_s)
        except Exception as exc:
            stale = self._cache.get(slug)
            if stale is None:
                raise DirectoryUnavailable(f"cannot resolve {slug!r}: {_reason(exc)}") from exc
            stale.expires_at = self._clock() + STALE_RETRY_S
            log.warning("platform lookup for %r failed (%s): serving what was read %.0f s ago",
                        slug, _reason(exc), self._clock() - stale.read_at)
            return stale.tenant
        return entry.tenant

    async def list(self) -> list[TenantSummary]:
        """Test tenants first, then active platform clients, each by display name.

        Never fails: with Postgres down it lists the platform clients last read,
        or none, rather than hiding the test tenants too.
        """
        summaries = [
            TenantSummary(t.slug, t.display_name, t.locale, "test", 0)
            for t in self.test_tenants.values()
        ]
        for row in await self._platform_rows():
            if not row.is_active or row.slug in self.test_tenants:
                continue
            summaries.append(TenantSummary(row.slug, display_name(row), PLATFORM_LOCALE,
                                           "platform", row.documents))
        summaries.sort(key=lambda s: (s.source != "test", s.display_name.casefold(), s.slug))
        return summaries

    async def close(self) -> None:
        close = getattr(self.platform, "close", None)
        if close is not None:
            await close()

    # -- internals ------------------------------------------------------------

    async def _platform_rows(self) -> PlatformRows:
        if self.platform is None:
            return []
        listing = self._listing
        if listing is not None and listing.expires_at > self._clock():
            return listing.rows
        try:
            return await self._bounded("listing", self._read_listing, self.listing_timeout_s)
        except Exception as exc:
            if listing is None:
                log.warning("platform tenants unavailable (%s): listing test tenants only",
                            _reason(exc))
                return []
            listing.expires_at = self._clock() + STALE_RETRY_S
            log.warning("platform tenants unavailable (%s): listing what was read %.0f s ago",
                        _reason(exc), self._clock() - listing.read_at)
            return listing.rows

    async def _read_tenant(self, slug: str) -> _Entry:
        assert self.platform is not None
        row = await self.platform.fetch(slug)
        tenant = platform_tenant(row) if row is not None and row.is_active else None
        now = self._clock()
        entry = _Entry(tenant, now, now + (FOUND_TTL_S if tenant else NOT_FOUND_TTL_S))
        self._remember(slug, entry)
        return entry

    async def _read_listing(self) -> PlatformRows:
        assert self.platform is not None
        rows = await self.platform.fetch_active()
        for row in rows:
            if row.slug in self.test_tenants and (row.slug, row.id) not in self._collisions:
                self._collisions.add((row.slug, row.id))
                log.warning("platform client %s has the slug %r of a test tenant: the test "
                            "tenant wins, and the client is not callable", row.id, row.slug)
        now = self._clock()
        self._listing = _Listing(rows, now, now + LISTING_TTL_S)
        return rows

    def _remember(self, slug: str, entry: _Entry) -> None:
        self._cache.pop(slug, None)          # re-inserted last: dict order is age
        self._cache[slug] = entry
        if len(self._cache) <= MAX_CACHED_SLUGS:
            return
        # Drop expired misses first (probes of random slugs), then the oldest
        # entries. Expired hits are what stale-if-error serves, so they go last.
        now = self._clock()
        for key in [k for k, e in self._cache.items() if e.tenant is None and e.expires_at <= now]:
            del self._cache[key]
        while len(self._cache) > MAX_CACHED_SLUGS:
            del self._cache[next(iter(self._cache))]

    async def _bounded[T](self, key: str, read: Callable[[], Awaitable[T]],
                          timeout_s: float) -> T:
        """Run read() at most once per key at a time, and wait for it at most timeout_s.

        Past the timeout the caller moves on and the query is left to finish in
        the background, still filling the cache. Cancelling it instead would
        block: psycopg waits for the server to acknowledge a cancelled query.
        """
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(read())
            self._inflight[key] = task
            task.add_done_callback(lambda t: self._landed(key, t))
        done, _ = await asyncio.wait({task}, timeout=timeout_s)
        if not done:
            raise TimeoutError(f"no answer within {timeout_s:.1f} s")
        return task.result()

    def _landed(self, key: str, task: asyncio.Future[Any]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.debug("platform query %s failed: %s", key, _reason(exc))


class PostgresPlatformSource:
    """Platform tenants from the portal's Postgres, read as voice_reader.

    A small pool, opened without waiting: the orchestrator starts and serves the
    test tenants whether or not Postgres is up yet.
    """

    def __init__(self, dsn: str, *, timeout_s: float = LOOKUP_TIMEOUT_S, max_size: int = 4):
        try:
            conninfo_to_dict(dsn)
        except psycopg.Error:
            # `from None`: libpq's message can quote the string, password included.
            raise ValueError("PLATFORM_DATABASE_URL is not a valid Postgres DSN") from None
        self.target = describe_dsn(dsn)
        statement_ms = int(timeout_s * 1000)

        async def configure(conn: psycopg.AsyncConnection[Any]) -> None:
            # Postgres enforces the bound too, so a slow query ends cleanly on the
            # server instead of holding a connection after the caller gave up.
            await conn.execute("SELECT set_config('statement_timeout', %s, false)",
                               (str(statement_ms),))

        self._pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            dsn,
            open=False,
            name="platform-tenants",
            min_size=1,
            max_size=max_size,
            kwargs={
                "autocommit": True,
                "connect_timeout": max(2, round(timeout_s)),   # libpq's minimum is 2 s
                "application_name": "orchestrator",
            },
            configure=configure,
            # A connection broken by a Postgres restart is replaced, not handed out.
            check=AsyncConnectionPool.check_connection,
            timeout=timeout_s,
            # Give up a failing reconnect loop early; the next lookup starts a new
            # one. The default (5 min, backing off) could leave a recovered
            # Postgres unused for a minute or more.
            reconnect_timeout=10.0,
        )

    async def open(self) -> None:
        await self._pool.open(wait=False)

    async def close(self) -> None:
        await self._pool.close()

    async def fetch(self, slug: str) -> PlatformTenant | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(_TENANT_SQL, (slug,))
            row = await cur.fetchone()
        if row is None:
            return None
        return PlatformTenant(id=str(row[0]), name=row[1], slug=row[2], is_active=bool(row[3]))

    async def fetch_active(self) -> list[PlatformTenant]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(_LISTING_SQL)
            rows = await cur.fetchall()
        return [
            PlatformTenant(id=str(r[0]), name=r[1], slug=r[2], is_active=bool(r[3]),
                           documents=int(r[4]))
            for r in rows
        ]


def describe_dsn(dsn: str) -> str:
    """user@host:port/dbname of a DSN, for logs. Never the password."""
    try:
        p = conninfo_to_dict(dsn)
    except psycopg.Error:
        return "<invalid DSN>"
    return (f"{p.get('user') or '?'}@{p.get('host') or 'localhost'}:{p.get('port') or 5432}"
            f"/{p.get('dbname') or '?'}")


def _reason(exc: BaseException) -> str:
    """One line for the log: the exception type, and its first line if short."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text[:120]}" if text else type(exc).__name__
