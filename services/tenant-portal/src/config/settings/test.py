"""Test settings: ephemeral RSA keys and secrets, no external services."""

import base64
import os
import secrets
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _write_ephemeral_keys() -> tuple[str, str]:
    """Generate a throwaway RSA key pair so tests never touch ./keys."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_dir = Path(tempfile.mkdtemp(prefix="jwt-test-keys-"))
    private_path = key_dir / "private.pem"
    public_path = key_dir / "public.pem"
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return str(private_path), str(public_path)


os.environ["JWT_PRIVATE_KEY_PATH"], os.environ["JWT_PUBLIC_KEY_PATH"] = _write_ephemeral_keys()
os.environ["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(50)
os.environ["MFA_ENCRYPTION_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ.setdefault("DATABASE_URL", "postgres://portal:portal@db:5432/tenant_portal")

from .base import *  # noqa: E402, F403
from .base import REST_FRAMEWORK  # noqa: E402

DEBUG = False
ALLOWED_HOSTS = ["testserver"]
FRONTEND_URL = "http://frontend.test"
CORS_ALLOWED_ORIGINS = ["http://frontend.test"]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
REFRESH_COOKIE_SECURE = False
MFA_REQUIRED_FOR_PLATFORM_ADMIN = True

# django-otp's exponential back-off would make multi-step tests time dependent.
OTP_TOTP_THROTTLE_FACTOR = 0
OTP_STATIC_THROTTLE_FACTOR = 0

# Generous limits so functional tests are not throttled; throttling itself
# has a dedicated test that lowers the rate.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        scope: "1000/minute" for scope in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    },
}
