"""Tenant slugs: a new company never gets a slug the voice platform reserves.

The voice platform's test tenants (tenants/*.yaml) own their slugs and win a
collision, so a client given one of them could never be called.

ISO 25010: Functional correctness, Security (integrity).
Runs against a real PostgreSQL test database (created and destroyed by pytest-django).
"""

import re

import pytest
from rest_framework.test import APIClient

from accounts.models import Tenant, User
from accounts.services import registration
from accounts.services.registration import create_tenant

SUFFIX = "[a-z0-9]{6}"

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("company", "reserved"),
    [("Acme", "acme"), ("SwiftNet", "swiftnet"), ("MaisonVerte", "maisonverte")],
)
def test_reserved_slug_is_never_assigned(company, reserved):
    slug = create_tenant(company).slug

    assert re.fullmatch(rf"{reserved}-{SUFFIX}", slug)
    assert not Tenant.objects.filter(slug=reserved).exists()


def test_registration_api_never_assigns_a_reserved_slug():
    response = APIClient().post(
        "/api/auth/register/",
        {
            "company_name": "Acme",
            "email": "owner@acme.test",
            "password": "Corr3ct-Horse-Battery!",
            "first_name": "Amira",
            "last_name": "Haddad",
        },
        format="json",
    )

    assert response.status_code == 201
    slug = User.objects.get(email="owner@acme.test").tenant.slug
    assert re.fullmatch(rf"acme-{SUFFIX}", slug)


def test_other_names_keep_their_plain_slug():
    assert create_tenant("Acme Delivery").slug == "acme-delivery"
    assert create_tenant("Maison Verte").slug == "maison-verte"


def test_reserved_slugs_come_from_settings(settings):
    # Entries are normalised like generated slugs (case, spaces).
    settings.RESERVED_TENANT_SLUGS = [" Clinic "]

    assert create_tenant("Acme").slug == "acme"
    assert re.fullmatch(rf"clinic-{SUFFIX}", create_tenant("Clinic").slug)


def test_taken_slug_gets_a_suffix():
    Tenant.objects.create(name="Clinic", slug="clinic")

    assert re.fullmatch(rf"clinic-{SUFFIX}", create_tenant("Clinic").slug)


def test_reserved_suffixed_slug_is_skipped_too(settings, monkeypatch):
    settings.RESERVED_TENANT_SLUGS = ["acme", "acme-aaaaaa"]
    suffixes = iter(["aaaaaa", "bbbbbb"])
    monkeypatch.setattr(registration, "get_random_string", lambda *_: next(suffixes))

    assert create_tenant("Acme").slug == "acme-bbbbbb"
