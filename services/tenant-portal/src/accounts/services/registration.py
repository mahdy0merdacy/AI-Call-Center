"""Tenant registration, email verification and Google-user onboarding."""

import logging
from dataclasses import dataclass

from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils.crypto import get_random_string
from django.utils.text import slugify

from accounts.models import Tenant, User, normalize_email_address
from accounts.services import emails
from accounts.tokens import email_verification_token, get_user_from_uid
from audit.services import EventType, log_auth_event

logger = logging.getLogger(__name__)

SLUG_MAX_BASE_LENGTH = 50
SLUG_SUFFIX_LENGTH = 6
SLUG_MAX_ATTEMPTS = 5


class OnboardingError(Exception):
    """The user cannot be onboarded (already has a tenant)."""


@dataclass(frozen=True)
class RegistrationData:
    company_name: str
    email: str
    password: str
    first_name: str
    last_name: str


def create_tenant(name: str) -> Tenant:
    """Create a tenant with a unique slug derived from its name."""
    base = slugify(name)[:SLUG_MAX_BASE_LENGTH].strip("-") or "tenant"
    slug = base
    for _ in range(SLUG_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                return Tenant.objects.create(name=name, slug=slug)
        except IntegrityError:
            suffix = get_random_string(SLUG_SUFFIX_LENGTH, "abcdefghijklmnopqrstuvwxyz0123456789")
            slug = f"{base}-{suffix}"
    raise RuntimeError("Could not generate a unique tenant slug.")


def register_tenant_admin(data: RegistrationData, request: HttpRequest) -> None:
    """Create Tenant + tenant_admin atomically and send the verification email.

    If the email is taken, no account is created; the owner gets a notice
    email instead. Both paths hash a password and send one email, so they
    look the same from outside (no account enumeration by response or timing).
    """
    email = normalize_email_address(data.email)
    if User.objects.filter(email=email).exists():
        _handle_duplicate_registration(email, data.password)
        return
    try:
        with transaction.atomic():
            tenant = create_tenant(data.company_name)
            user = User.objects.create_user(
                email=email,
                password=data.password,
                first_name=data.first_name,
                last_name=data.last_name,
                role=User.Role.TENANT_ADMIN,
                tenant=tenant,
                is_email_verified=False,
            )
    except IntegrityError:
        # Lost a race with a concurrent registration of the same email.
        _handle_duplicate_registration(email, data.password)
        return
    log_auth_event(EventType.REGISTER, request=request, user=user, tenant=tenant)
    emails.send_verification_email(user)


def _handle_duplicate_registration(email: str, password: str) -> None:
    make_password(password)  # same hashing cost as a real registration
    logger.info("registration attempted for an existing email")
    emails.send_account_exists_email(email)


def verify_email(uidb64: str, token: str, request: HttpRequest) -> bool:
    """Mark the email as verified if the token is valid. Single use."""
    user = get_user_from_uid(uidb64)
    if user is None or not email_verification_token.check_token(user, token):
        return False
    user.is_email_verified = True
    user.save(update_fields=["is_email_verified", "updated_at"])
    log_auth_event(EventType.EMAIL_VERIFIED, request=request, user=user)
    return True


def resend_verification(email: str) -> None:
    """Send a new verification link if the account exists and is unverified."""
    user = User.objects.filter(email=normalize_email_address(email)).first()
    if user is not None and user.is_active and not user.is_email_verified:
        emails.send_verification_email(user)


def complete_onboarding(user: User, company_name: str) -> User:
    """Create a tenant for a Google user and attach it."""
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if not locked.needs_onboarding:
            raise OnboardingError("Onboarding already completed.")
        locked.tenant = create_tenant(company_name)
        locked.save(update_fields=["tenant", "updated_at"])
    return locked
