"""DRF permission classes for roles, onboarding and MFA enrollment.

`AccountSetupComplete` is part of DEFAULT_PERMISSION_CLASSES and the base of
every role permission, so any view is closed to users who still have to
finish onboarding (tenant_admin without tenant) or MFA enrollment
(platform_admin, when MFA_REQUIRED_FOR_PLATFORM_ADMIN). Views reachable in
those states opt out explicitly by listing only `IsAuthenticated`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from rest_framework.permissions import BasePermission

from accounts.models import User

if TYPE_CHECKING:
    # Runtime imports would be circular: DRF loads this module while
    # rest_framework.views is still initializing.
    from rest_framework.request import Request
    from rest_framework.views import APIView


def _is_authenticated(request: Request) -> bool:
    return bool(request.user and request.user.is_authenticated)


class AccountSetupComplete(BasePermission):
    """User has a tenant (tenant_admin) or an MFA device when required (admin)."""

    message = "Account setup is incomplete."
    code = "account_setup_incomplete"

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not _is_authenticated(request):
            return False
        user: User = request.user
        if user.needs_onboarding:
            self.message, self.code = "Onboarding required.", "onboarding_required"
            return False
        if (
            user.role == User.Role.PLATFORM_ADMIN
            and settings.MFA_REQUIRED_FOR_PLATFORM_ADMIN
            and not user.mfa_enabled
        ):
            self.message, self.code = "MFA enrollment required.", "mfa_enrollment_required"
            return False
        return True


class IsPlatformAdmin(AccountSetupComplete):
    message = "Platform admin role required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return (
            super().has_permission(request, view)
            and request.user.role == User.Role.PLATFORM_ADMIN
        )


class IsTenantAdmin(AccountSetupComplete):
    message = "Tenant admin role required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return (
            super().has_permission(request, view)
            and request.user.role == User.Role.TENANT_ADMIN
        )


class HasTenant(AccountSetupComplete):
    """User belongs to a tenant (onboarding complete). Excludes platform admins."""

    message = "A tenant is required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return super().has_permission(request, view) and request.user.tenant_id is not None


class NeedsOnboarding(BasePermission):
    """Tenant admin who has not created a tenant yet (new Google user)."""

    message = "Onboarding already completed."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _is_authenticated(request) and request.user.needs_onboarding


class IsPlatformAdminRole(BasePermission):
    """Role check only, without the setup gate (used by MFA enrollment)."""

    message = "Platform admin role required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _is_authenticated(request) and request.user.role == User.Role.PLATFORM_ADMIN


class RequiresCustomHeader(BasePermission):
    """Require `X-Requested-With: XMLHttpRequest` on cookie-based endpoints.

    A cross-site form cannot set custom headers, and a cross-origin fetch
    that does triggers a CORS preflight that only allowed origins pass.
    """

    message = "Missing required X-Requested-With header."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return request.headers.get(settings.AUTH_CUSTOM_HEADER) == settings.AUTH_CUSTOM_HEADER_VALUE
