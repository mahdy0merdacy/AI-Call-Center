"""Test (a) - unit: password hashing and JWT validation.

ISO 25010: Security (integrity, authenticity), Functional correctness.
No database needed: only hashing functions and token signing are used.
"""

import base64
import json
from datetime import timedelta

import pytest
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError

from accounts.jwt import PortalAccessToken

PASSWORD = "Corr3ct-Horse-Battery!"


def test_password_is_hashed_with_argon2_and_verifies():
    hashed = make_password(PASSWORD)

    assert hashed.startswith("argon2$")  # stored as Argon2, never in clear text
    assert PASSWORD not in hashed
    assert check_password(PASSWORD, hashed) is True
    assert check_password("Wrong-Password-123!", hashed) is False


def _new_access_token(**claims) -> PortalAccessToken:
    token = PortalAccessToken()
    token["sub"] = "00000000-0000-0000-0000-000000000001"
    token["role"] = "tenant_admin"
    for key, value in claims.items():
        token[key] = value
    return token


def test_valid_access_token_is_accepted():
    raw = str(_new_access_token())

    decoded = PortalAccessToken(raw)  # verifies signature, expiry, issuer, audience

    assert decoded["role"] == "tenant_admin"


def test_expired_access_token_is_rejected():
    token = _new_access_token()
    token.set_exp(from_time=timezone.now() - timedelta(hours=1))

    with pytest.raises(TokenError):
        PortalAccessToken(str(token))


def test_tampered_access_token_is_rejected():
    """Change the role in the payload but keep the original signature."""
    header, payload, signature = str(_new_access_token()).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["role"] = "platform_admin"  # privilege escalation attempt
    forged_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()

    with pytest.raises(TokenError):
        PortalAccessToken(f"{header}.{forged_payload}.{signature}")
