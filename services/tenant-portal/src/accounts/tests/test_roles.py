"""Test (c) - role-based access: a tenant_admin cannot use platform-admin endpoints.

ISO 25010: Security (confidentiality, integrity).
"""

import pytest
from rest_framework.test import APIClient

from accounts.models import Tenant, User

EMAIL = "admin@clinic.test"
PASSWORD = "Corr3ct-Horse-Battery!"

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_admin_client():
    """A verified tenant_admin, logged in through the real login endpoint."""
    tenant = Tenant.objects.create(name="Clinic", slug="clinic")
    User.objects.create_user(
        email=EMAIL,
        password=PASSWORD,
        role=User.Role.TENANT_ADMIN,
        tenant=tenant,
        is_email_verified=True,
    )
    api = APIClient()
    response = api.post("/api/auth/login/", {"email": EMAIL, "password": PASSWORD}, format="json")
    assert response.status_code == 200
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access_token']}")
    return api


def test_tenant_admin_gets_403_on_platform_admin_endpoint(tenant_admin_client):
    # Positive control: the token works on an endpoint this role is allowed to use,
    # so the 403 below comes from the role check, not from a broken login.
    assert tenant_admin_client.get("/api/tenant/me/").status_code == 200

    response = tenant_admin_client.get("/api/admin/tenants/")

    assert response.status_code == 403
    assert response.json()["detail"] == "Platform admin role required."
