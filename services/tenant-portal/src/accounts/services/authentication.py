"""Credential checks, lockout, token issuance, refresh rotation and logout."""

from __future__ import annotations

import logging

from axes.handlers.proxy import AxesProxyHandler
from django.conf import settings
from django.contrib.auth import user_logged_in, user_login_failed
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenBackendError, TokenError
from rest_framework_simplejwt.settings import api_settings

from accounts.jwt import PortalRefreshToken, TokenPair, issue_token_pair
from accounts.models import User, normalize_email_address
from audit.services import EventType, log_auth_event

logger = logging.getLogger(__name__)

GENERIC_LOGIN_ERROR = "Invalid email or password."


class InvalidCredentials(Exception):
    """Login failed. Always reported to the client as GENERIC_LOGIN_ERROR."""


class InvalidRefreshToken(Exception):
    """Refresh cookie missing, invalid, expired, reused or user blocked."""


def axes_lockout_response(request: HttpRequest, *args, **kwargs) -> HttpResponse:
    """django-axes lockout handler: same response as any other login failure."""
    return JsonResponse({"detail": GENERIC_LOGIN_ERROR}, status=401)


def authenticate_credentials(request: HttpRequest, email: str, password: str) -> User:
    """Check email + password with lockout and timing-attack protection.

    Every path that does not reach a real password check still computes one
    Argon2 hash, so response times do not reveal whether the account exists.
    Raises InvalidCredentials on any failure.
    """
    email = normalize_email_address(email)
    credentials = {"username": email}

    if not AxesProxyHandler.is_allowed(request, credentials):
        make_password(password)
        log_auth_event(EventType.LOGIN_FAILED, request=request)
        raise InvalidCredentials

    user = User.objects.select_related("tenant").filter(email=email).first()
    if user is None or user.is_locked or not user.has_usable_password():
        make_password(password)
        log_auth_event(EventType.LOGIN_FAILED, request=request, user=user)
        _notify_axes_failure(request, email)
        raise InvalidCredentials

    if not user.check_password(password):
        _register_failed_attempt(user, request)
        _notify_axes_failure(request, email)
        raise InvalidCredentials

    # Correct password, but the account may not log in. Same generic error:
    # the password was right, so a distinct message would only help the
    # owner, but we keep one message for all failures (see README).
    if not user.is_email_verified or not user.can_authenticate():
        log_auth_event(EventType.LOGIN_FAILED, request=request, user=user)
        raise InvalidCredentials

    if user.failed_login_attempts or user.locked_until:
        User.objects.filter(pk=user.pk).update(failed_login_attempts=0, locked_until=None)
        user.failed_login_attempts, user.locked_until = 0, None
    return user


def _notify_axes_failure(request: HttpRequest, email: str) -> None:
    user_login_failed.send(sender=__name__, credentials={"username": email}, request=request)


def _register_failed_attempt(user: User, request: HttpRequest) -> None:
    """Increment the failure counter; lock the account at the threshold."""
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        locked_user.failed_login_attempts += 1
        now_locked = locked_user.failed_login_attempts >= settings.LOGIN_LOCKOUT_THRESHOLD
        if now_locked:
            locked_user.locked_until = timezone.now() + settings.LOGIN_LOCKOUT_DURATION
            locked_user.failed_login_attempts = 0
        locked_user.save(update_fields=["failed_login_attempts", "locked_until", "updated_at"])
    log_auth_event(EventType.LOGIN_FAILED, request=request, user=user)
    if now_locked:
        log_auth_event(EventType.ACCOUNT_LOCKED, request=request, user=user)


def complete_login(request: HttpRequest, user: User, event_type: str) -> TokenPair:
    """Final step of every login path: signals, audit event, fresh tokens."""
    # Updates last_login (Django receiver) and resets django-axes counters.
    user_logged_in.send(sender=user.__class__, request=request, user=user)
    log_auth_event(event_type, request=request, user=user)
    return issue_token_pair(user)


def rotate_refresh_token(raw_token: str | None, request: HttpRequest) -> TokenPair:
    """Exchange a refresh token for a new pair; the old one is blacklisted.

    Claims are rebuilt from the database, so role/tenant changes (e.g. after
    onboarding) are reflected. Blacklisting is an atomic get_or_create on the
    token's jti: of two concurrent uses of the same token, only one wins.
    """
    token = _load_refresh_token(raw_token)
    user = (
        User.objects.select_related("tenant")
        .filter(pk=token[api_settings.USER_ID_CLAIM])
        .first()
    )
    if user is None or not user.is_email_verified or not user.can_authenticate():
        raise InvalidRefreshToken
    if api_settings.ROTATE_REFRESH_TOKENS and api_settings.BLACKLIST_AFTER_ROTATION:
        _, created = token.blacklist()
        if not created:
            raise InvalidRefreshToken
    log_auth_event(EventType.TOKEN_REFRESH, request=request, user=user)
    return issue_token_pair(user)


def logout(raw_token: str | None, request: HttpRequest) -> None:
    """Blacklist the refresh token if it is valid. Never fails."""
    try:
        token = _load_refresh_token(raw_token)
    except InvalidRefreshToken:
        return
    token.blacklist()
    user = User.objects.filter(pk=token[api_settings.USER_ID_CLAIM]).first()
    log_auth_event(EventType.LOGOUT, request=request, user=user)


def revoke_refresh_cookie(raw_token: str | None) -> None:
    """Blacklist the caller's current refresh token, if any (e.g. before reissuing)."""
    try:
        _load_refresh_token(raw_token).blacklist()
    except InvalidRefreshToken:
        pass


def _load_refresh_token(raw_token: str | None) -> PortalRefreshToken:
    if not raw_token:
        raise InvalidRefreshToken
    try:
        return PortalRefreshToken(raw_token)
    except (TokenError, TokenBackendError) as exc:
        raise InvalidRefreshToken from exc
