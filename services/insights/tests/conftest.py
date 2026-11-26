"""Test fixtures: a real Postgres with the analytics contract, and signed tokens.

Postgres comes from INSIGHTS_TEST_ADMIN_URL (a superuser DSN, as CI's postgres
service provides) or else from a throwaway `docker run postgres:18`, removed at
the end of the session. Two databases are created: `insights_data` with fixture
rows for two tenants, and `insights_empty` with the tables only (both dropped and
recreated, so never point it at a server that matters). The app connects as a
read-only role, `insights_test`, like the `insights` role in production.
"""

import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from app.config import Settings
from app.main import create_app

TODAY = date(2026, 6, 30)
D0 = TODAY
D1 = TODAY - timedelta(days=1)
D2 = TODAY - timedelta(days=2)
PREV = TODAY - timedelta(days=46)  # in the 30 days before the default range
OLD = TODAY - timedelta(days=90)  # outside both

PORTAL_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
PORTAL_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
PORTAL_C = uuid.UUID("33333333-3333-4333-8333-333333333333")  # no links, no data
DATA_A = "acme-delivery"
DATA_B = "swiftnet-isp"
A_OWN = str(PORTAL_A)  # rows keyed by the portal tenant id itself

ISSUER = "tenant-portal"
AUDIENCE = "ai-voice-platform"
INSIGHTS_PASSWORD = "insights-test"
IMAGE = "postgres:18"


# ── postgres ──────────────────────────────────────────────────────────────────────


def _wait_for(dsn: str, timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def admin_dsn() -> Iterator[str]:
    url = os.environ.get("INSIGHTS_TEST_ADMIN_URL")
    if url:
        _wait_for(url)
        yield url
        return
    if not shutil.which("docker"):
        pytest.skip("needs Docker or INSIGHTS_TEST_ADMIN_URL (a Postgres superuser DSN)")
    name = f"insights-test-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-p",
            "127.0.0.1::5432",
            IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    try:
        port = (
            subprocess.run(
                ["docker", "port", name, "5432/tcp"], check=True, capture_output=True, text=True
            )
            .stdout.split(":")[-1]
            .strip()
        )
        url = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
        _wait_for(url)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _db_dsn(admin: str, dbname: str, user: str, password: str) -> str:
    return make_conninfo(admin, dbname=dbname, user=user, password=password)


@pytest.fixture(scope="session")
def databases(admin_dsn: str) -> dict[str, str]:
    schema = (Path(__file__).parent / "schema.sql").read_text()
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for db in ("insights_data", "insights_empty"):
            conn.execute(f"DROP DATABASE IF EXISTS {db}")
            conn.execute(f"CREATE DATABASE {db}")
        conn.execute("DROP ROLE IF EXISTS insights_test")
        conn.execute(f"CREATE ROLE insights_test LOGIN PASSWORD '{INSIGHTS_PASSWORD}'")
    out = {}
    for db in ("insights_data", "insights_empty"):
        with psycopg.connect(make_conninfo(admin_dsn, dbname=db), autocommit=True) as conn:
            conn.execute(schema)
            if db == "insights_data":
                _seed(conn)
        out[db] = _db_dsn(admin_dsn, db, "insights_test", INSIGHTS_PASSWORD)
    return out


def _insert(conn: psycopg.Connection, table: str, rows: list[tuple[Any, ...]]) -> None:
    marks = ", ".join(["%s"] * len(rows[0]))
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO analytics.{table} VALUES ({marks})", rows)


def _td(
    tenant: str,
    day: date,
    syn: bool,
    calls: int,
    ended: int,
    esc: int,
    dur: float | None,
    p50: int | None,
) -> tuple[Any, ...]:
    # The stored per-day rates are deliberately wrong: the API must not use them.
    return (
        tenant,
        day,
        syn,
        calls,
        ended,
        esc,
        0.123,
        0.456,
        dur,
        3.0,
        0,
        0.789,
        p50,
        2000,
        0,
        0,
        0,
    )


def _seed(conn: psycopg.Connection) -> None:
    _insert(conn, "tenant_links", [(PORTAL_A, DATA_A), (PORTAL_B, DATA_B)])
    _insert(
        conn,
        "publish_runs",
        [
            ("r1", datetime(2026, 6, 29, 3, 0, tzinfo=UTC), json.dumps(["tenant_daily"])),
            ("r2", datetime(2026, 6, 30, 3, 0, tzinfo=UTC), json.dumps(["tenant_daily"])),
        ],
    )
    _insert(
        conn,
        "tenant_daily",
        [
            _td(DATA_A, D0, False, 10, 10, 2, 60.0, 800),
            _td(DATA_A, D1, True, 5, 4, 1, 120.0, 1000),
            _td(DATA_A, PREV, False, 4, 4, 4, 30.0, 500),
            _td(DATA_A, OLD, False, 100, 100, 0, 10.0, 100),
            _td(A_OWN, D0, False, 1, 1, 0, 30.0, 600),
            _td(DATA_B, D0, False, 1000, 1000, 500, 999.0, 9999),
        ],
    )
    _insert(
        conn,
        "tool_daily",
        [
            (DATA_A, D0, False, "lookup_shipment", 8, 6, 1, 1, 100.0),
            (DATA_A, D1, True, "lookup_shipment", 2, 2, 0, 0, 200.0),
            (DATA_A, D0, False, "create_ticket", 4, 4, 0, 0, 50.0),
            (DATA_A, OLD, False, "create_ticket", 50, 0, 50, 0, 5.0),
            (DATA_B, D0, False, "b_secret_tool", 70, 1, 69, 0, 1.0),
        ],
    )
    _insert(
        conn,
        "sentiment_daily",
        [
            (DATA_A, D0, False, "neutral", 10),
            (DATA_A, D0, False, "angry", 2),
            (DATA_A, D1, True, "neutral", 3),
            (DATA_A, D1, True, "satisfied", 5),
            (DATA_B, D0, False, "sad", 99),
        ],
    )
    _insert(
        conn,
        "call_sentiment_daily",
        [
            (DATA_A, D0, False, 11, 2, 3, 6, 2, 1),
            (DATA_A, D1, True, 5, 1, 1, 3, 1, 0),
            (DATA_B, D0, False, 500, 400, 0, 0, 500, 400),
        ],
    )
    _insert(
        conn,
        "knowledge_daily",
        [
            (DATA_A, D0, False, 10, 7, 2, 1, 40.0),
            (DATA_A, D1, True, 5, 5, 0, 0, 30.0),
            (DATA_B, D0, False, 80, 0, 80, 0, 10.0),
        ],
    )
    _insert(
        conn,
        "document_usage",
        [
            (DATA_A, D0, False, "Delivery policy", 5),
            (DATA_A, D1, True, "Delivery policy", 2),
            (DATA_A, D1, True, "Returns", 3),
            (DATA_B, D0, False, "B internal manual", 50),
        ],
    )
    _insert(
        conn,
        "knowledge_gaps",
        [
            (DATA_A, D0, False, "Do you ship to Mars?", 2, 0.2),
            (DATA_A, D2, False, "Do you ship to Mars?", 1, 0.1),
            (DATA_A, D1, True, "Can I pay in gold?", 1, 0.3),
            (DATA_B, D0, False, "B secret question", 9, 0.1),
        ],
    )
    _insert(
        conn,
        "hourly",
        [
            (DATA_A, D0, False, 9, 6),
            (DATA_A, D0, False, 14, 5),
            (DATA_A, D1, True, 9, 5),
            (DATA_B, D0, False, 9, 1000),
        ],
    )
    transcript = json.dumps(
        [
            {"who": "caller", "text": "Where is my parcel?"},
            {"who": "agent", "text": "Let me check."},
        ]
    )
    _insert(
        conn,
        "calls_to_review",
        [
            (
                DATA_A,
                "a-real",
                datetime(2026, 6, 30, 10, 0, tzinfo=UTC),
                False,
                "escalated",
                "angry",
                61.5,
                6,
                transcript,
            ),
            (
                DATA_A,
                "a-sim",
                datetime(2026, 6, 29, 8, 0, tzinfo=UTC),
                True,
                "worsening",
                None,
                30.0,
                3,
                json.dumps([]),
            ),
            (
                DATA_A,
                "a-old",
                datetime(2026, 4, 1, 8, 0, tzinfo=UTC),
                False,
                "escalated",
                "sad",
                10.0,
                2,
                json.dumps([]),
            ),
            (
                DATA_B,
                "b-call",
                datetime(2026, 6, 30, 11, 0, tzinfo=UTC),
                False,
                "escalated",
                "angry",
                1.0,
                1,
                json.dumps([{"who": "caller", "text": "B secret"}]),
            ),
        ],
    )


# ── tokens ────────────────────────────────────────────────────────────────────────


def _pem_pair() -> tuple[bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public.decode()


@pytest.fixture(scope="session")
def keys() -> dict[str, Any]:
    private, public = _pem_pair()
    other_private, _ = _pem_pair()
    return {"private": private, "public": public, "other_private": other_private}


@pytest.fixture(scope="session")
def make_token(keys: dict[str, Any]) -> Callable[..., str]:
    def make(
        role: str = "tenant_admin",
        tenant: uuid.UUID | str | None = PORTAL_A,
        *,
        key: bytes | None = None,
        algorithm: str = "RS256",
        **overrides: Any,
    ) -> str:
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": now + timedelta(minutes=5),
            "iat": now,
            "token_type": "access",
            "user_id": "1",
            "tenant_id": str(tenant) if tenant is not None else None,
            "role": role,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not ...}
        return jwt.encode(claims, key or keys["private"], algorithm=algorithm)

    return make


@pytest.fixture(scope="session")
def auth(make_token: Callable[..., str]) -> Callable[..., dict[str, str]]:
    def headers(*args: Any, **kwargs: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(*args, **kwargs)}"}

    return headers


# ── app ───────────────────────────────────────────────────────────────────────────


def _client(dsn: str, public_key: str) -> TestClient:
    settings = Settings(database_url=dsn, jwt_public_key=public_key)
    return TestClient(create_app(settings, clock=lambda: TODAY))


@pytest.fixture()
def client(databases: dict[str, str], keys: dict[str, Any]) -> Iterator[TestClient]:
    with _client(databases["insights_data"], keys["public"]) as c:
        yield c


@pytest.fixture()
def empty_client(databases: dict[str, str], keys: dict[str, Any]) -> Iterator[TestClient]:
    with _client(databases["insights_empty"], keys["public"]) as c:
        yield c


@pytest.fixture()
def make_client(keys: dict[str, Any]) -> Callable[..., TestClient]:
    def make(dsn: str, public_key: str | None = None) -> TestClient:
        return _client(dsn, keys["public"] if public_key is None else public_key)

    return make
