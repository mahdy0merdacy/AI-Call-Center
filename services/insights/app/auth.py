"""Who is calling: the portal user named in a verified access token.

Same rules as services/documents/documents/authentication.py: the portal signs
access tokens with its private key (RS256); this service only holds the public
key, so it can check tokens but never mint one. The tenant always comes from the
token, never from the request, for a tenant admin.

  missing / bad / expired token -> 401
  valid token, role not allowed -> 403
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# Fixed on purpose, not a setting: accepting only RS256 rules out "algorithm
# confusion" (an HS256 token signed with the public key as the secret).
ALGORITHMS = ["RS256"]

TENANT_ADMIN = "tenant_admin"
PLATFORM_ADMIN = "platform_admin"
ALLOWED_ROLES = (TENANT_ADMIN, PLATFORM_ADMIN)


@dataclass(frozen=True)
class Caller:
    role: str
    # The portal tenant. Set for a tenant admin, None for a platform admin.
    tenant_id: uuid.UUID | None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def usable_public_key(pem: str) -> bool:
    try:
        return isinstance(load_pem_public_key(pem.encode()), rsa.RSAPublicKey)
    except (ValueError, UnsupportedAlgorithm):
        return False


def bearer_token(authorization: str | None) -> str:
    parts = (authorization or "").split()
    if not parts or parts[0].lower() != "bearer":
        raise _unauthorized("Authentication credentials were not provided.")
    if len(parts) != 2:
        raise _unauthorized("Invalid Authorization header.")
    return parts[1]


def verify(token: str, *, public_key: str, issuer: str, audience: str) -> Caller:
    if not public_key:
        logger.error("JWT_PUBLIC_KEY is not configured; refusing all requests.")
        raise _unauthorized("Authentication is not configured.")
    claims = _decode(token, public_key=public_key, issuer=issuer, audience=audience)

    # A refresh token is signed by the same key but lives for days: only the
    # short-lived access token may call an API.
    if claims.get("token_type", "access") != "access":
        raise _unauthorized("Invalid token.")

    # A known caller with the wrong role is refused (403), not unknown (401).
    # Checked before the tenant: a platform admin has none.
    role = claims.get("role")
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account role cannot use the insights service.",
        )
    if role == PLATFORM_ADMIN:
        return Caller(role=PLATFORM_ADMIN, tenant_id=None)

    try:
        tenant_id = uuid.UUID(str(claims.get("tenant_id")))
    except ValueError:
        raise _unauthorized("Token does not carry a valid tenant.") from None
    return Caller(role=TENANT_ADMIN, tenant_id=tenant_id)


def _decode(token: str, *, public_key: str, issuer: str, audience: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            public_key,
            algorithms=ALGORITHMS,
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token has expired.") from None
    except (jwt.PyJWTError, ValueError):
        # Bad signature, wrong issuer/audience, malformed, missing claim, unusable
        # key... One generic message: don't tell an attacker which check failed.
        raise _unauthorized("Invalid token.") from None
