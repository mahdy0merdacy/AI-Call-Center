"""django-allauth adapters enforcing the portal's Google sign-in rules.

Rules:
* Only Google, only with `email_verified: true` in the ID token.
* Users are identified by Google `sub` (SocialAccount.uid), never by email.
* No automatic linking: if the Google email belongs to an existing portal
  account that is not already linked to this Google `sub`, the login is
  refused with `error=email_already_registered`. The user signs in with
  their password instead. (Linking an existing account to Google is not
  offered yet; see README.)
* New users become tenant_admin with no tenant, a verified email and an
  unusable password; they must call /api/auth/onboarding/ next.

A ValidationError raised here is turned by allauth headless into a redirect
to the frontend callback URL with `?error=<code>`.
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from allauth.socialaccount.providers.base.constants import AuthProcess
from django.core.exceptions import ValidationError
from django.http import HttpRequest

from accounts.models import User, normalize_email_address

ALLOWED_PROVIDERS = {"google"}


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        """Local signup goes through /api/auth/register/, never allauth."""
        return False


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest, sociallogin: SocialLogin) -> bool:
        return True

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin) -> None:
        """Validate the provider response before any login or signup happens."""
        account = sociallogin.account
        if account.provider not in ALLOWED_PROVIDERS:
            raise ValidationError("Provider not allowed.", code="provider_not_allowed")
        if sociallogin.state.get("process") == AuthProcess.CONNECT:
            raise ValidationError("Account linking is not supported.", code="connect_not_supported")
        if account.extra_data.get("email_verified") is not True:
            raise ValidationError("Google email is not verified.", code="email_not_verified")
        if sociallogin.is_existing:
            return  # matched by Google `sub`
        email = normalize_email_address(account.extra_data.get("email") or "")
        if not email:
            raise ValidationError("Google did not provide an email.", code="email_missing")
        if User.objects.filter(email=email).exists():
            raise ValidationError(
                "An account with this email already exists; sign in with your password.",
                code="email_already_registered",
            )

    def populate_user(self, request: HttpRequest, sociallogin: SocialLogin, data: dict) -> User:
        user = super().populate_user(request, sociallogin, data)
        user.email = normalize_email_address(user.email)
        user.role = User.Role.TENANT_ADMIN
        user.tenant = None
        user.is_email_verified = True
        return user
