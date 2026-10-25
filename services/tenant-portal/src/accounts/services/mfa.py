"""TOTP enrollment, backup codes and the login MFA challenge.

django-otp is session oriented; here its devices are used only to store
secrets and verify codes, and the JWT flow is implemented on top.
"""

from __future__ import annotations

import base64
import io
import re
import secrets
from dataclasses import dataclass

import qrcode
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from accounts.models import BackupCodeDevice, EncryptedTOTPDevice, MFAChallenge, User
from audit.services import EventType, log_auth_event

MFA_TOKEN_SALT = "accounts.mfa-challenge"
TOTP_CODE_RE = re.compile(r"^\d{6}$")
# No 0/o, 1/l/i: easy to read back from paper.
BACKUP_CODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
BACKUP_CODE_LENGTH = 10


class MFAError(Exception):
    """Base class for MFA failures."""


class AlreadyEnrolled(MFAError):
    pass


class NoPendingEnrollment(MFAError):
    pass


class InvalidMFACode(MFAError):
    pass


class InvalidMFAToken(MFAError):
    """mfa_token expired, tampered with, already used or out of attempts."""


@dataclass(frozen=True)
class Enrollment:
    otpauth_uri: str
    qr_code_png_base64: str


# --- Enrollment -------------------------------------------------------------


def start_totp_enrollment(user: User) -> Enrollment:
    """Create an unconfirmed device (replacing any pending one)."""
    if user.mfa_enabled:
        raise AlreadyEnrolled
    EncryptedTOTPDevice.objects.filter(user=user, confirmed=False).delete()
    device = EncryptedTOTPDevice.create_unconfirmed(user)
    uri = device.config_url
    return Enrollment(otpauth_uri=uri, qr_code_png_base64=_qr_png_base64(uri))


def confirm_totp_enrollment(user: User, code: str, request: HttpRequest) -> list[str]:
    """Verify the first code, confirm the device, return new backup codes once."""
    with transaction.atomic():
        device = (
            EncryptedTOTPDevice.objects.select_for_update()
            .filter(user=user, confirmed=False)
            .order_by("-id")
            .first()
        )
        if device is None:
            raise NoPendingEnrollment
        if not device.verify_token(code):
            raise InvalidMFACode
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        codes = regenerate_backup_codes(user)
    log_auth_event(EventType.MFA_ENROLLED, request=request, user=user)
    return codes


def regenerate_backup_codes(user: User) -> list[str]:
    """Replace the user's backup codes; the plaintext is only returned here."""
    device, _ = BackupCodeDevice.objects.get_or_create(
        user=user, defaults={"name": "Backup codes", "confirmed": True}
    )
    codes = [_new_backup_code() for _ in range(settings.MFA_BACKUP_CODE_COUNT)]
    device.set_codes(codes)
    return codes


def _new_backup_code() -> str:
    raw = "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(BACKUP_CODE_LENGTH))
    return f"{raw[:5]}-{raw[5:]}"


def _qr_png_base64(data: str) -> str:
    buffer = io.BytesIO()
    qrcode.make(data).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


# --- Login challenge --------------------------------------------------------


def create_mfa_challenge(user: User) -> str:
    """Start the second step of a login; returns the opaque `mfa_token`.

    The token is a Django-signed reference (not a JWT), so it can never be
    accepted as an access token.
    """
    challenge = MFAChallenge.objects.create(
        user=user, expires_at=timezone.now() + settings.MFA_TOKEN_TTL
    )
    return signing.dumps(
        {"cid": str(challenge.pk), "uid": str(user.pk)}, salt=MFA_TOKEN_SALT, compress=True
    )


def verify_mfa_challenge(mfa_token: str, code: str, request: HttpRequest) -> User:
    """Check a TOTP or backup code against a pending challenge.

    Each call consumes one of MFA_MAX_ATTEMPTS attempts; success consumes the
    challenge. Rows are locked so concurrent guesses cannot exceed the limit
    and one TOTP code cannot be accepted twice.
    """
    payload = _load_mfa_token(mfa_token)
    with transaction.atomic():
        challenge = (
            # Lock only the challenge row: Postgres rejects FOR UPDATE on the
            # nullable side of the LEFT JOIN to tenant (platform admins).
            MFAChallenge.objects.select_for_update(of=("self",))
            .select_related("user", "user__tenant")
            .filter(pk=payload["cid"], user_id=payload["uid"])
            .first()
        )
        if challenge is None or not _challenge_usable(challenge):
            raise InvalidMFAToken
        user = challenge.user
        challenge.attempts += 1
        verified = user.can_authenticate() and _verify_code(user, code)
        if verified:
            challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["attempts", "consumed_at"])
    if not verified:
        log_auth_event(EventType.MFA_FAILED, request=request, user=user)
        raise InvalidMFACode
    log_auth_event(EventType.MFA_SUCCESS, request=request, user=user)
    return user


def _load_mfa_token(mfa_token: str) -> dict:
    try:
        payload = signing.loads(
            mfa_token,
            salt=MFA_TOKEN_SALT,
            max_age=settings.MFA_TOKEN_TTL.total_seconds(),
        )
    except signing.BadSignature as exc:  # includes SignatureExpired
        raise InvalidMFAToken from exc
    if not isinstance(payload, dict) or not {"cid", "uid"} <= payload.keys():
        raise InvalidMFAToken
    return payload


def _challenge_usable(challenge: MFAChallenge) -> bool:
    return (
        challenge.consumed_at is None
        and challenge.expires_at > timezone.now()
        and challenge.attempts < settings.MFA_MAX_ATTEMPTS
    )


def _verify_code(user: User, code: str) -> bool:
    """6 digits -> TOTP (one step tolerance, anti-replay); else backup code."""
    code = code.strip()
    if TOTP_CODE_RE.match(code):
        device = (
            EncryptedTOTPDevice.objects.select_for_update()
            .filter(user=user, confirmed=True)
            .first()
        )
        return device is not None and device.verify_token(code)
    backup = BackupCodeDevice.objects.filter(user=user, confirmed=True).first()
    return backup is not None and backup.verify_token(code)
