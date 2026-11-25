"""Google sign-in token exchange and onboarding."""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.jwt import issue_token_pair
from accounts.permissions import NeedsOnboarding, RequiresCustomHeader
from accounts.serializers import (
    AccessTokenResponseSerializer,
    DetailSerializer,
    OnboardingSerializer,
)
from accounts.services import registration
from accounts.services.authentication import revoke_refresh_cookie
from accounts.services.google import NoGoogleLogin, exchange_google_session
from accounts.views.common import PublicAPIView, detail_response, get_refresh_cookie, token_response


class GoogleExchangeView(PublicAPIView):
    permission_classes = [RequiresCustomHeader]

    @extend_schema(
        request=None,
        responses={200: AccessTokenResponseSerializer, 401: DetailSerializer},
        tags=["google"],
    )
    def post(self, request: Request) -> Response:
        """Exchange the allauth session from a Google login for portal JWTs.

        Call once, right after allauth redirects back to the frontend
        callback URL. The session is destroyed. Requires `X-Requested-With`.
        """
        try:
            _, pair = exchange_google_session(request._request)
        except NoGoogleLogin:
            return detail_response("No Google login to exchange.", status.HTTP_401_UNAUTHORIZED)
        return token_response(pair)


class OnboardingView(APIView):
    permission_classes = [IsAuthenticated, NeedsOnboarding]

    @extend_schema(
        request=OnboardingSerializer,
        responses={201: AccessTokenResponseSerializer, 409: DetailSerializer},
        tags=["google"],
    )
    def post(self, request: Request) -> Response:
        """Create the tenant for a new Google user; returns tokens with `tenant_id`."""
        serializer = OnboardingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = registration.complete_onboarding(
                request.user, serializer.validated_data["company_name"]
            )
        except registration.OnboardingError:
            return detail_response("Onboarding already completed.", status.HTTP_409_CONFLICT)
        # The old refresh token has tenant_id=null; retire it.
        revoke_refresh_cookie(get_refresh_cookie(request))
        return token_response(issue_token_pair(user), status.HTTP_201_CREATED)
