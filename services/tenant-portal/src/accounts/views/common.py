"""Response helpers shared by the auth views."""

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.jwt import TokenPair, access_token_lifetime_seconds, set_refresh_cookie
from accounts.services.authentication import GENERIC_LOGIN_ERROR


class PublicAPIView(APIView):
    """Endpoint usable without a token. Ignores any Authorization header, so a
    stale access token cannot break login or refresh."""

    authentication_classes: list = []
    permission_classes = [AllowAny]


def token_response(pair: TokenPair, http_status: int = status.HTTP_200_OK) -> Response:
    """Access token in the body, refresh token in an HttpOnly cookie."""
    response = Response(
        {
            "access_token": pair.access,
            "token_type": "Bearer",
            "expires_in": access_token_lifetime_seconds(),
        },
        status=http_status,
    )
    set_refresh_cookie(response, pair.refresh)
    response["Cache-Control"] = "no-store"
    return response


def login_failed_response() -> Response:
    return Response({"detail": GENERIC_LOGIN_ERROR}, status=status.HTTP_401_UNAUTHORIZED)


def detail_response(message: str, http_status: int = status.HTTP_200_OK) -> Response:
    return Response({"detail": message}, status=http_status)


def get_refresh_cookie(request: Request) -> str | None:
    return request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
