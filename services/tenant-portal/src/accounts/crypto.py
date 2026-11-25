"""Protection of MFA secrets at rest.

Both keys are derived with HKDF from MFA_ENCRYPTION_KEY, so a database dump
alone reveals neither TOTP secrets nor backup codes.

* TOTP secrets are encrypted with AES-SIV (deterministic authenticated
  encryption). A 20-byte secret becomes 36 bytes, i.e. 72 hex characters,
  which fits django-otp's existing 80-character `key` column, so no schema
  change to django-otp is needed. The user id is bound as associated data,
  so a ciphertext copied to another user's row fails to decrypt.
* Backup codes are only ever compared, never shown again, so they are stored
  as a keyed HMAC truncated to 16 hex characters (StaticToken's column size).
"""

import base64
import binascii
import hashlib
import hmac
from functools import cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_MIN_MASTER_KEY_BYTES = 32


def _master_key() -> bytes:
    try:
        key = base64.b64decode(settings.MFA_ENCRYPTION_KEY, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImproperlyConfigured("MFA_ENCRYPTION_KEY must be base64.") from exc
    if len(key) < _MIN_MASTER_KEY_BYTES:
        raise ImproperlyConfigured("MFA_ENCRYPTION_KEY must decode to at least 32 bytes.")
    return key


@cache
def _derive(info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(
        _master_key()
    )


def _totp_cipher() -> AESSIV:
    return AESSIV(_derive(b"tenant-portal/totp-secret/v1", 64))


def encrypt_totp_secret(secret: bytes, user_id: object) -> str:
    """Encrypt a raw TOTP secret for storage in `TOTPDevice.key` (hex)."""
    return _totp_cipher().encrypt(secret, [str(user_id).encode()]).hex()


def decrypt_totp_secret(stored: str, user_id: object) -> bytes:
    """Decrypt a value produced by `encrypt_totp_secret`."""
    return _totp_cipher().decrypt(bytes.fromhex(stored), [str(user_id).encode()])


def hash_backup_code(code: str) -> str:
    """Keyed digest of a normalized backup code (16 hex chars)."""
    key = _derive(b"tenant-portal/backup-codes/v1", 32)
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()[:16]
