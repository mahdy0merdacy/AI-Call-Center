"""Probes, and what happens when Postgres is unreachable."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app

# Nothing listens on port 1: every connection attempt is refused at once.
DOWN = "postgresql://insights:x@127.0.0.1:1/aicc"


@pytest.fixture()
def down_client(keys):
    settings = Settings(database_url=DOWN, jwt_public_key=keys["public"], query_timeout_s=1)
    with TestClient(create_app(settings)) as c:
        yield c


def test_probes_need_no_token(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}


def test_not_ready_without_database(down_client):
    assert down_client.get("/healthz").status_code == 200  # the process itself is fine
    assert down_client.get("/readyz").status_code == 503


def test_database_down_is_503_without_details(down_client, auth):
    r = down_client.get("/api/insights/summary", headers=auth())
    assert r.status_code == 503
    # The driver's message (host, role, SQL) is logged by type only, never returned.
    assert r.json() == {"detail": "Analytics are unavailable, try again shortly."}


def test_auth_is_checked_before_the_database(down_client):
    assert down_client.get("/api/insights/summary").status_code == 401


def test_bad_dsn_does_not_echo_the_password():
    with pytest.raises(ValueError) as exc:
        Database("host=db password=hunter2 nonsense")
    assert "hunter2" not in str(exc.value)


def test_role_is_read_only(databases):
    import psycopg

    with psycopg.connect(databases["insights_data"]) as conn, pytest.raises(psycopg.Error):
        conn.execute("DELETE FROM analytics.tenant_links")
