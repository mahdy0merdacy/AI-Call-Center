"""Transactional emails. Delivery failures are logged, never surfaced.

Surfacing a delivery error would make responses differ between existing and
unknown accounts, so callers always get the same generic response.
"""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from accounts.models import User
from accounts.tokens import email_verification_token, encode_uid, password_reset_token

logger = logging.getLogger(__name__)


def _frontend_link(path: str, **params: str) -> str:
    return f"{settings.FRONTEND_URL}{path}?{urlencode(params)}"


def _send(to: str, subject: str, template: str, context: dict) -> None:
    body = render_to_string(f"accounts/email/{template}.txt", context)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to])
    except Exception:  # noqa: BLE001 - any SMTP failure must stay invisible to the client
        logger.exception("email delivery failed", extra={"template": template})


def send_verification_email(user: User) -> None:
    link = _frontend_link(
        "/verify-email", uid=encode_uid(user), token=email_verification_token.make_token(user)
    )
    _send(user.email, "Verify your email address", "verify_email", {"user": user, "link": link})


def send_account_exists_email(email: str) -> None:
    """Sent instead of a second account when someone registers an existing email."""
    context = {
        "login_link": f"{settings.FRONTEND_URL}/login",
        "reset_link": f"{settings.FRONTEND_URL}/forgot-password",
    }
    _send(email, "You already have an account", "account_exists", context)


def send_password_reset_email(user: User) -> None:
    link = _frontend_link(
        "/reset-password", uid=encode_uid(user), token=password_reset_token.make_token(user)
    )
    _send(user.email, "Reset your password", "password_reset", {"user": user, "link": link})
