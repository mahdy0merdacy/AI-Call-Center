"""RS256 JWTs: signing with a `kid` header, custom claims, JWKS, cookies.

simplejwt 5.5 has no option to add a `kid` header, so `KidTokenBackend`
overrides `encode()`, and the portal token classes use that backend.
Verification is unchanged (the header is simply ignored by simplejwt).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from functools import cache
from typing import Any

import jwt as pyjwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from django.conf import settings
from django.http import HttpResponse
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.utils import datetime_from_epoch

from accounts.models import User


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@cache
def public_jwk() -> dict[str, str]:
    """The verifying key as a JWK, with an RFC 7638 thumbprint as `kid`."""
    public_key = load_pem_public_key(api_settings.VERIFYING_KEY.encode())
    numbers = public_key.public_numbers()
    jwk = {"e": _b64url_uint(numbers.e), "kty": "RSA", "n": _b64url_uint(numbers.n)}
    thumbprint = hashlib.sha256(
        json.dumps(jwk, separators=(",", ":"), sort_keys=True).encode()
    ).digest()
    kid = base64.urlsafe_b64encode(thumbprint).rstrip(b"=").decode()
    return {**jwk, "kid": kid, "use": "sig", "alg": api_settings.ALGORITHM}


class KidTokenBackend(TokenBackend):
    """TokenBackend that adds a `kid` header so verifiers can pick the key."""

    def encode(self, payload: dict[str, Any]) -> str:
        jwt_payload = payload.copy()
        if self.audience is not None:
            jwt_payload["aud"] = self.audience
        if self.issuer is not None:
            jwt_payload["iss"] = self.issuer
        return pyjwt.encode(
            jwt_payload,
            self.prepared_signing_key,
            algorithm=self.algorithm,
            headers={"kid": public_jwk()["kid"]},
            json_encoder=self.json_encoder,
        )


@cache
def get_token_backend() -> KidTokenBackend:
    return KidTokenBackend(
        api_settings.ALGORITHM,
        api_settings.SIGNING_KEY,
        api_settings.VERIFYING_KEY,
        api_settings.AUDIENCE,
        api_settings.ISSUER,
        api_settings.JWK_URL,
        api_settings.LEEWAY,
        api_settings.JSON_ENCODER,
    )


class _PortalBackendMixin:
    @property
    def token_backend(self) -> KidTokenBackend:
        return get_token_backend()

    def get_token_backend(self) -> KidTokenBackend:
        return get_token_backend()


class PortalAccessToken(_PortalBackendMixin, AccessToken):
    """Access token (15 min). Custom claims are copied from the refresh token."""


class PortalRefreshToken(_PortalBackendMixin, RefreshToken):
    """Refresh token (7 days) carrying `tenant_id`, `role`, `email_verified`."""

    access_token_class = PortalAccessToken

    @classmethod
    def for_user(cls, user: User) -> PortalRefreshToken:
        """Build a refresh token and register it as outstanding.

        Unlike simplejwt's default, the token string itself is not stored in
        the OutstandingToken table: only its jti and expiry are needed for the
        blacklist, and a stored token would be usable if the DB leaked.
        """
        token = cls()
        token[api_settings.USER_ID_CLAIM] = str(user.pk)
        token["tenant_id"] = str(user.tenant_id) if user.tenant_id else None
        token["role"] = user.role
        token["email_verified"] = user.is_email_verified
        OutstandingToken.objects.create(
            user=user,
            jti=token[api_settings.JTI_CLAIM],
            token="",
            created_at=token.current_time,
            expires_at=datetime_from_epoch(token["exp"]),
        )
        return token


@dataclass(frozen=True)
class TokenPair:
    access: str
    refresh: str


def issue_token_pair(user: User) -> TokenPair:
    """Issue a fresh access/refresh pair with up-to-date claims."""
    refresh = PortalRefreshToken.for_user(user)
    return TokenPair(access=str(refresh.access_token), refresh=str(refresh))


def revoke_all_refresh_tokens(user: User) -> int:
    """Blacklist every outstanding refresh token of `user`. Returns the count."""
    outstanding = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    blacklisted = BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in outstanding], ignore_conflicts=True
    )
    return len(blacklisted)


def access_token_lifetime_seconds() -> int:
    return int(api_settings.ACCESS_TOKEN_LIFETIME.total_seconds())


def set_refresh_cookie(response: HttpResponse, refresh: str) -> None:
    """Attach the refresh token as an HttpOnly, SameSite=Strict cookie."""
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        refresh,
        max_age=int(api_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
        path=settings.REFRESH_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN,
        secure=settings.REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )


def clear_refresh_cookie(response: HttpResponse) -> None:
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )
