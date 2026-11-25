"""Signed, expiring, single-use tokens for email links.

Built on Django's PasswordResetTokenGenerator: the token is an HMAC over a
hash value that includes user state, so it stops validating as soon as that
state changes (that is what makes the tokens single-use).
"""
# make verification token

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import ValidationError
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes, force_str
from django.utils.http import base36_to_int, urlsafe_base64_decode, urlsafe_base64_encode

from accounts.models import User


class ExpiringTokenGenerator(PasswordResetTokenGenerator):
    """PasswordResetTokenGenerator with a per-generator timeout.

    Django's `check_token` always uses PASSWORD_RESET_TIMEOUT; this version
    uses `timeout_setting` instead. Logic otherwise mirrors Django's.
    """

    timeout_setting = "PASSWORD_RESET_TIMEOUT"

    @property
    def timeout(self) -> int:
        return getattr(settings, self.timeout_setting)

    def check_token(self, user: User | None, token: str | None) -> bool:
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split("-")
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False
        return (self._num_seconds(self._now()) - ts) <= self.timeout


class EmailVerificationTokenGenerator(ExpiringTokenGenerator):
    """Valid 24 h; invalidated once `is_email_verified` flips to True."""

    key_salt = "accounts.tokens.EmailVerificationTokenGenerator"
    timeout_setting = "EMAIL_VERIFICATION_TIMEOUT"

    def _make_hash_value(self, user: User, timestamp: int) -> str:
        return f"{user.pk}{user.email}{user.is_email_verified}{timestamp}"


class PortalPasswordResetTokenGenerator(ExpiringTokenGenerator):
    """Valid 1 h; invalidated by a password change or a new login."""

    key_salt = "accounts.tokens.PasswordResetTokenGenerator"
    timeout_setting = "PASSWORD_RESET_TIMEOUT"


email_verification_token = EmailVerificationTokenGenerator()
password_reset_token = PortalPasswordResetTokenGenerator()


def encode_uid(user: User) -> str:
    return urlsafe_base64_encode(force_bytes(user.pk))


def get_user_from_uid(uidb64: str) -> User | None:
    """Resolve a base64 uid from an email link, or None if invalid."""
    try:
        return User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, ValidationError, User.DoesNotExist):
        return None
