"""Password reset (request + confirm)."""

from axes.handlers.proxy import AxesProxyHandler
from django.db import transaction
from django.http import HttpRequest

from accounts.jwt import revoke_all_refresh_tokens
from accounts.models import User, normalize_email_address
from accounts.services import emails
from accounts.tokens import get_user_from_uid, password_reset_token
from audit.services import EventType, log_auth_event


def request_password_reset(email: str, request: HttpRequest) -> None:
    """Email a reset link if an eligible account exists. Silent otherwise.

    Accounts without a usable password (Google sign-in only) are skipped, as
    in Django's PasswordResetForm: they authenticate with Google.
    """
    user = User.objects.filter(email=normalize_email_address(email)).first()
    if user is None or not user.is_active or not user.has_usable_password():
        return
    log_auth_event(EventType.PASSWORD_RESET_REQUESTED, request=request, user=user)
    emails.send_password_reset_email(user)


def get_reset_user(uidb64: str, token: str) -> User | None:
    """Return the user if the reset token is valid, else None."""
    user = get_user_from_uid(uidb64)
    if user is None or not user.is_active or not password_reset_token.check_token(user, token):
        return None
    return user


def confirm_password_reset(user: User, new_password: str, request: HttpRequest) -> None:
    """Set the new password, clear lockouts and sign out every session.

    The password change itself invalidates the reset token (its hash covers
    the password hash), so the link is single-use.
    """
    with transaction.atomic():
        user.set_password(new_password)
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["password", "failed_login_attempts", "locked_until", "updated_at"])
        revoke_all_refresh_tokens(user)
    AxesProxyHandler.reset_attempts(username=user.email)
    log_auth_event(EventType.PASSWORD_RESET_DONE, request=request, user=user)
