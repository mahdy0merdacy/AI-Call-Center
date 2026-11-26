"""Who may read what: tokens, roles and tenant isolation."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import DATA_A, DATA_B, PORTAL_A, PORTAL_B, PORTAL_C

ENDPOINTS = ["summary", "sentiment", "tools", "knowledge", "activity", "calls"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_token_is_401(client, endpoint):
    r = client.get(f"/api/insights/{endpoint}")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Token abc", "Bearer", "Bearer a b", "Basic dXNlcjpwdw=="])
def test_malformed_header_is_401(client, header):
    assert client.get("/api/insights/summary", headers={"Authorization": header}).status_code == 401


def test_garbage_token_is_401(client):
    r = client.get("/api/insights/summary", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},  # expired
        {"iss": "someone-else"},
        {"aud": "another-api"},
        {"iss": ...},  # claim missing
        {"aud": ...},
        {"exp": ...},
        {"token_type": "refresh"},  # 7-day token, not an access one
    ],
)
def test_bad_claims_are_401(client, auth, overrides):
    assert client.get("/api/insights/summary", headers=auth(**overrides)).status_code == 401


def test_token_signed_by_another_key_is_401(client, auth, keys):
    r = client.get("/api/insights/summary", headers=auth(key=keys["other_private"]))
    assert r.status_code == 401


def test_hs256_with_public_key_as_secret_is_401(client, keys):
    # Algorithm confusion: an attacker knows the public key and signs with it as an
    # HMAC secret. PyJWT refuses to build such a token, so it is built by hand.
    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(
        json.dumps(
            {
                "iss": "tenant-portal",
                "aud": "ai-voice-platform",
                "exp": exp,
                "role": "tenant_admin",
                "tenant_id": str(PORTAL_A),
            }
        ).encode()
    )
    signature = b64(
        hmac.new(keys["public"].encode(), header + b"." + payload, hashlib.sha256).digest()
    )
    token = b".".join([header, payload, signature]).decode()
    r = client.get("/api/insights/summary", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.parametrize("tenant", [None, "not-a-uuid", ""])
def test_tenant_admin_without_valid_tenant_is_401(client, auth, tenant):
    assert client.get("/api/insights/summary", headers=auth(tenant=tenant)).status_code == 401


@pytest.mark.parametrize("role", ["tenant_member", "viewer", "", None])
def test_other_roles_are_403(client, auth, role):
    assert client.get("/api/insights/summary", headers=auth(role=role)).status_code == 403


def test_unconfigured_key_refuses_everything(make_client, databases, auth):
    with make_client(databases["insights_data"], public_key="") as c:
        assert c.get("/api/insights/summary", headers=auth()).status_code == 401


def test_usable_public_key(keys):
    from app.auth import usable_public_key

    assert usable_public_key(keys["public"])
    assert not usable_public_key("")
    assert not usable_public_key("-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----")


def test_key_with_literal_newlines_from_env(monkeypatch, keys):
    from app.config import Settings

    monkeypatch.setenv("ANALYTICS_DATABASE_URL", "postgresql://insights@db/aicc")
    monkeypatch.setenv("JWT_PUBLIC_KEY", keys["public"].replace("\n", "\\n"))
    monkeypatch.delenv("JWT_ISSUER", raising=False)
    monkeypatch.setenv("JWT_AUDIENCE", "")
    s = Settings.from_env()
    assert s.jwt_public_key == keys["public"].strip()
    assert (s.jwt_issuer, s.jwt_audience) == ("tenant-portal", "ai-voice-platform")


def test_missing_database_url_fails_start(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("ANALYTICS_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        Settings.from_env()


# ── isolation ────────────────────────────────────────────────────────────────────


def test_tenant_admin_sees_own_and_linked_tenants(client, auth):
    body = client.get("/api/insights/summary", headers=auth(tenant=PORTAL_A)).json()
    assert body["tenants"] == [str(PORTAL_A), DATA_A]


def test_tenant_admin_cannot_pick_another_tenant(client, auth):
    # ?tenant is for platform admins; a tenant admin's tenant comes from the token.
    r = client.get(f"/api/insights/summary?tenant={DATA_B}", headers=auth(tenant=PORTAL_A))
    assert r.status_code == 200
    assert DATA_B not in r.json()["tenants"]
    assert r.json()["kpis"]["calls"] == 16


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_tenant_a_never_sees_tenant_b(client, auth, endpoint):
    text = client.get(f"/api/insights/{endpoint}", headers=auth(tenant=PORTAL_A)).text
    for secret in ("b_secret_tool", "B internal manual", "B secret", "sad", "b-call"):
        assert secret not in text


def test_tenant_b_sees_only_its_data(client, auth):
    h = auth(tenant=PORTAL_B)
    assert client.get("/api/insights/summary", headers=h).json()["kpis"]["calls"] == 1000
    tools = client.get("/api/insights/tools", headers=h).json()["tools"]
    assert [t["tool"] for t in tools] == ["b_secret_tool"]
    calls = client.get("/api/insights/calls", headers=h).json()["calls"]
    assert [c["call_id"] for c in calls] == ["b-call"]


def test_tenant_without_links_sees_nothing(client, auth):
    body = client.get("/api/insights/summary", headers=auth(tenant=PORTAL_C)).json()
    assert body["tenants"] == [str(PORTAL_C)]
    assert body["kpis"]["calls"] == 0


def test_platform_admin_must_name_a_tenant(client, auth):
    h = auth(role="platform_admin", tenant=None)
    assert client.get("/api/insights/summary", headers=h).status_code == 400
    assert client.get("/api/insights/summary?tenant=bad%20id", headers=h).status_code == 400


def test_platform_admin_reads_the_named_tenant(client, auth):
    h = auth(role="platform_admin", tenant=None)
    body = client.get(f"/api/insights/summary?tenant={DATA_B}", headers=h).json()
    assert body["tenants"] == [DATA_B]
    assert body["kpis"]["calls"] == 1000
    # A data tenant id, not a portal one: links are not followed.
    body = client.get(f"/api/insights/summary?tenant={DATA_A}", headers=h).json()
    assert body["kpis"]["calls"] == 15
