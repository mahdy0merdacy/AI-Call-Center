"""Test (b) - integration/API: register -> verify email -> login -> protected endpoint.

ISO 25010: Functional suitability (completeness, correctness), Security (authenticity).
Runs against a real PostgreSQL test database (created and destroyed by pytest-django).
"""

import re
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from rest_framework.test import APIClient

EMAIL = "owner@clinic.test"
PASSWORD = "Corr3ct-Horse-Battery!"

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def _register_and_verify(api: APIClient) -> None:
    response = api.post(
        "/api/auth/register/",
        {
            "company_name": "Test Clinic",
            "email": EMAIL,
            "password": PASSWORD,
            "first_name": "Sara",
            "last_name": "Ben",
        },
        format="json",
    )
    assert response.status_code == 201

    # The verification link is read from the test mailbox, like a real user would.
    assert len(mail.outbox) == 1
    link = re.search(r"https?://\S+/verify-email\?\S+", mail.outbox[0].body).group(0)
    params = parse_qs(urlparse(link).query)
    response = api.post(
        "/api/auth/verify-email/",
        {"uid": params["uid"][0], "token": params["token"][0]},
        format="json",
    )
    assert response.status_code == 200


def _login(api: APIClient, password: str):
    return api.post("/api/auth/login/", {"email": EMAIL, "password": password}, format="json")


def test_register_login_and_call_protected_endpoint(api):
    _register_and_verify(api)

    response = _login(api, PASSWORD)
    assert response.status_code == 200
    assert response.json()["token_type"] == "Bearer"
    assert "refresh_token" in response.cookies  # refresh token only in the HttpOnly cookie
    assert response.cookies["refresh_token"]["httponly"]
    access = response.json()["access_token"]

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    response = api.get("/api/auth/me/")
    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


def test_wrong_password_returns_401(api):
    _register_and_verify(api)

    response = _login(api, "Wrong-Password-123!")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password."}
    assert "access_token" not in response.json()


def test_protected_endpoint_without_token_returns_401(api):
    response = api.get("/api/auth/me/")

    assert response.status_code == 401
