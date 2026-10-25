"""Turn a completed allauth Google login (session) into portal JWTs."""

import time

from allauth.account.authentication import get_authentication_records
from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest

from accounts.jwt import TokenPair
from accounts.models import User
from accounts.services.authentication import complete_login
from audit.services import EventType


class NoGoogleLogin(Exception):
    """No recent, successful Google login in this session."""


def exchange_google_session(request: HttpRequest) -> tuple[User, TokenPair]:
    """Issue our own tokens for the user allauth just logged in via Google.

    The allauth session is destroyed immediately, so it is single use and
    the rest of the API only ever sees JWTs.
    """
    # Read from the session: DRF has already replaced request.user with
    # AnonymousUser, because the exchange view has no authentication classes.
    user = get_user(request)
    records = get_authentication_records(request)
    last = records[-1] if records else {}
    is_recent_google = (
        last.get("method") == "socialaccount"
        and last.get("provider") == "google"
        and time.time() - last.get("at", 0) <= settings.GOOGLE_EXCHANGE_MAX_AGE.total_seconds()
    )
    django_logout(request)
    if not (user.is_authenticated and is_recent_google):
        raise NoGoogleLogin
    user = User.objects.select_related("tenant").get(pk=user.pk)
    if not user.can_authenticate():
        raise NoGoogleLogin
    return user, complete_login(request, user, EventType.GOOGLE_LOGIN)
