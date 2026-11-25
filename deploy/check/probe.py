"""One connectivity probe, run INSIDE the calling container by scripts/core-check.sh.

    docker compose exec -T <from> python -I - <kind> <args...> < deploy/check/probe.py

Prints one line, "OK|<ms>|<detail>" or "FAIL|<ms>|<detail>", and exits 0 or 1.
<ms> is the call alone (name lookup, connect, request), not Python's imports.

It needs only the standard library: each kind imports its client (grpc,
psycopg, pymongo, mcp) when it runs, so the same file works in every Python
image and uses the very libraries the service itself calls with. Arguments may
name the container's environment, e.g. '${KNOWLEDGE_URL}/healthz', so a probe
dials exactly what the service is configured to dial. Credentials stay inside
the container: they are never printed, and error text is scrubbed of them.

Kinds:
    tcp        HOST:PORT
    http       URL [EXPECTED_STATUS]
    post       URL JSON [EXPECTED_STATUS]
    grpc       HOST:PORT                      standard grpc.health.v1 Check
    pg         DSN SQL                        first row of SQL
    pg-denied  DSN SQL                        OK only if SQL is refused (42501)
    mongo      URI DB COLLECTION              document count
    smtp       HOST:PORT                      EHLO
    mcp        URL                            initialize + tools/list
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 5.0

_clock = [time.perf_counter()]


def start_clock() -> None:
    """Called by each probe once its imports are done: latency is the call alone."""
    _clock[0] = time.perf_counter()


class ProbeFailed(Exception):
    """The callee answered, but not what a healthy callee answers."""


def expand(value: str) -> str:
    """${NAME} and ${NAME:-default} from this container's environment."""

    def lookup(m: re.Match) -> str:
        found = os.environ.get(m.group(1))
        if found:
            return found
        if m.group(2) is not None:
            return m.group(2)
        raise ProbeFailed(f"${m.group(1)} is not set in this container")

    return re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", lookup, value)


def scrub(text: str) -> str:
    """One short line, without credentials embedded in URLs."""
    text = re.sub(r"://[^/@\s]+@", "://***@", str(text))
    return " ".join(text.replace("|", "/").split())[:140]


def where(url: str) -> str:
    """user@host:port/db of a DSN or URI, never its password."""
    u = urllib.parse.urlsplit(url)
    user = f"{u.username}@" if u.username else ""
    port = f":{u.port}" if u.port else ""
    return f"{user}{u.hostname}{port}{u.path}"


def split_hostport(target: str) -> tuple[str, int]:
    host, _, port = target.rpartition(":")
    return host, int(port)


def summary(body: bytes) -> str:
    """A few telling fields of a JSON body, e.g. status=ok dimensions=1024."""
    try:
        data = json.loads(body)
    except ValueError:
        return body[:60].decode("utf-8", "replace")
    if not isinstance(data, dict):
        return type(data).__name__
    parts = []
    for key, value in data.items():
        if isinstance(value, list):
            parts.append(f"{key}={len(value)}")
        elif not isinstance(value, dict):
            parts.append(f"{key}={value}")
    return " ".join(parts[:4])


# ── kinds ────────────────────────────────────────────────────────────────────


def probe_tcp(target: str) -> str:
    import socket

    host, port = split_hostport(expand(target))
    start_clock()
    with socket.create_connection((host, port), TIMEOUT_S):
        return f"connected to {host}:{port}"


def _http(url: str, data: bytes | None, expected: str) -> str:
    # urlopen also opens file: and ftp: URLs; a probe only ever means HTTP.
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ProbeFailed(f"not an http(s) URL: {url}")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    start_clock()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status, body = resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read(65536)
    detail = f"HTTP {status} {summary(body)}".strip()
    if str(status) != expected:
        raise ProbeFailed(f"{detail} (expected {expected})")
    return detail


def probe_http(url: str, expected: str = "200") -> str:
    return _http(expand(url), None, expected)


def probe_post(url: str, payload: str, expected: str = "200") -> str:
    return _http(expand(url), payload.encode(), expected)


def probe_grpc(target: str) -> str:
    # Raw call instead of grpc_health's stubs: not every image ships that
    # package, and the message is tiny. HealthCheckResponse is field 1, a
    # varint: b"\x08\x01" is SERVING; an empty message is UNKNOWN.
    import grpc

    target = expand(target)
    names = {0: "UNKNOWN", 1: "SERVING", 2: "NOT_SERVING", 3: "SERVICE_UNKNOWN"}
    start_clock()
    with grpc.insecure_channel(target) as channel:
        check = channel.unary_unary(
            "/grpc.health.v1.Health/Check",
            request_serializer=lambda b: b,
            response_deserializer=lambda b: b,
        )
        raw = check(b"", timeout=TIMEOUT_S)
    status = raw[1] if len(raw) >= 2 and raw[0] == 0x08 else 0
    detail = f"{names.get(status, status)} ({target})"
    if status != 1:
        raise ProbeFailed(detail)
    return detail


def _pg_row(dsn: str, sql: str) -> tuple:
    """First row of sql, with whichever PostgreSQL driver this image has."""
    try:
        import psycopg
    except ImportError:
        pass
    else:
        start_clock()
        with psycopg.connect(dsn, connect_timeout=int(TIMEOUT_S)) as conn:
            return tuple(conn.execute(sql).fetchone() or ())

    try:
        import asyncio

        import asyncpg
    except ImportError:
        pass
    else:

        async def fetch() -> tuple:
            conn = await asyncpg.connect(dsn, timeout=TIMEOUT_S)
            try:
                return tuple(await conn.fetchrow(sql) or ())
            finally:
                await conn.close()

        start_clock()
        return asyncio.run(fetch())

    try:
        import psycopg2
    except ImportError as exc:
        raise ProbeFailed("no PostgreSQL driver in this image") from exc
    start_clock()
    conn = psycopg2.connect(dsn, connect_timeout=int(TIMEOUT_S))
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return tuple(cur.fetchone() or ())
    finally:
        conn.close()


def probe_pg(dsn: str, sql: str) -> str:
    dsn = expand(dsn)
    row = _pg_row(dsn, sql)
    return f"{where(dsn)} -> {', '.join(str(v) for v in row)}"


def probe_pg_denied(dsn: str, sql: str) -> str:
    dsn = expand(dsn)
    try:
        _pg_row(dsn, sql)
    except ProbeFailed:
        raise
    except Exception as exc:
        code = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
        if code == "42501":  # insufficient_privilege
            return f"{where(dsn)} refused, as it should be (42501)"
        raise
    raise ProbeFailed(f"{where(dsn)} was ALLOWED: the role has too many privileges")


def probe_mongo(uri: str, db: str, collection: str) -> str:
    from pymongo import MongoClient

    uri, db = expand(uri), expand(db)
    start_clock()
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=int(TIMEOUT_S * 1000),
        connectTimeoutMS=int(TIMEOUT_S * 1000),
    )
    try:
        count = client[db][collection].estimated_document_count()
    finally:
        client.close()
    return f"{where(uri)} -> {count} in {collection}"


def probe_smtp(target: str) -> str:
    import smtplib

    host, port = split_hostport(expand(target))
    start_clock()
    with smtplib.SMTP(host, port, timeout=TIMEOUT_S) as smtp:
        code, message = smtp.ehlo()
    if code != 250:
        raise ProbeFailed(f"EHLO answered {code}")
    first = message.decode("utf-8", "replace").splitlines()[0]
    return f"EHLO {code} {first}"


def probe_mcp(url: str) -> str:
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = expand(url)

    async def session() -> str:
        # Same client calls as services/orchestrator/app/tools.py.
        async with (
            streamable_http_client(url) as streams,
            ClientSession(streams[0], streams[1]) as client,
        ):
            init = await client.initialize()
            tools = await client.list_tools()
        info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
        names = ",".join(t.name for t in tools.tools)
        return f"{getattr(info, 'name', '?')}: tools {names}"

    start_clock()
    return asyncio.run(asyncio.wait_for(session(), TIMEOUT_S * 2))


KINDS = {
    "tcp": probe_tcp,
    "http": probe_http,
    "post": probe_post,
    "grpc": probe_grpc,
    "pg": probe_pg,
    "pg-denied": probe_pg_denied,
    "mongo": probe_mongo,
    "smtp": probe_smtp,
    "mcp": probe_mcp,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in KINDS:
        print(f"FAIL|0|usage: probe.py {{{','.join(KINDS)}}} ARGS...")
        return 2
    start_clock()
    try:
        detail, ok = KINDS[sys.argv[1]](*sys.argv[2:]), True
    except ProbeFailed as exc:
        detail, ok = str(exc), False
    except Exception as exc:  # noqa: BLE001 - any failure is a result to report
        detail, ok = f"{type(exc).__name__}: {exc}", False
    ms = round((time.perf_counter() - _clock[0]) * 1000)
    print(f"{'OK' if ok else 'FAIL'}|{ms}|{scrub(detail)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
