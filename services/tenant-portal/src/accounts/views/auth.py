"""Email/password authentication endpoints."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.jwt import clear_refresh_cookie
from accounts.models import User
from accounts.permissions import RequiresCustomHeader
from accounts.serializers import (
    AccessTokenResponseSerializer,
    DetailSerializer,
    EmailSerializer,
    LoginSerializer,
    MeSerializer,
    MFARequiredResponseSerializer,
    PasswordResetConfirmSerializer,
    RegisterSerializer,
    UidTokenSerializer,
    validate_password_policy,
)
from accounts.services import authentication, mfa, passwords, registration
from accounts.views.common import (
    PublicAPIView,
    detail_response,
    get_refresh_cookie,
    login_failed_response,
    token_response,
)
from audit.services import EventType

REGISTER_MESSAGE = "If the details are valid, a verification email has been sent."
RESEND_MESSAGE = "If the account exists and is not verified, a new email has been sent."
RESET_MESSAGE = "If the account exists, a password reset email has been sent."
INVALID_LINK_MESSAGE = "Invalid or expired link."


class RegisterView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    @extend_schema(request=RegisterSerializer, responses={201: DetailSerializer}, tags=["auth"])
    def post(self, request: Request) -> Response:
        """Create a tenant and its first tenant_admin. No tokens are returned."""
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration.register_tenant_admin(
            registration.RegistrationData(**serializer.validated_data), request._request
        )
        return detail_response(REGISTER_MESSAGE, status.HTTP_201_CREATED)


class VerifyEmailView(PublicAPIView):
    @extend_schema(
        request=UidTokenSerializer,
        responses={200: DetailSerializer, 400: DetailSerializer},
        tags=["auth"],
    )
    def post(self, request: Request) -> Response:
        """Confirm an email address with the uid/token from the email link."""
        serializer = UidTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not registration.verify_email(data["uid"], data["token"], request._request):
            return detail_response(INVALID_LINK_MESSAGE, status.HTTP_400_BAD_REQUEST)
        return detail_response("Email verified.")


class ResendVerificationView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "resend_verification"

    @extend_schema(request=EmailSerializer, responses={200: DetailSerializer}, tags=["auth"])
    def post(self, request: Request) -> Response:
        """Resend the verification email. Always returns the same response."""
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration.resend_verification(serializer.validated_data["email"])
        return detail_response(RESEND_MESSAGE)


class LoginView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(
                AccessTokenResponseSerializer,
                description="Tokens issued (refresh token set as HttpOnly cookie), or "
                "`{mfa_required, mfa_token}` for platform admins with MFA.",
            ),
            401: DetailSerializer,
        },
        tags=["auth"],
    )
    def post(self, request: Request) -> Response:
        """Log in with email and password."""
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return login_failed_response()
        django_request = request._request
        try:
            user = authentication.authenticate_credentials(
                django_request,
                serializer.validated_data["email"],
                serializer.validated_data["password"],
            )
        except authentication.InvalidCredentials:
            return login_failed_response()
        if user.role == User.Role.PLATFORM_ADMIN and user.mfa_enabled:
            data = MFARequiredResponseSerializer(
                {"mfa_required": True, "mfa_token": mfa.create_mfa_challenge(user)}
            ).data
            return Response(data, headers={"Cache-Control": "no-store"})
        pair = authentication.complete_login(django_request, user, EventType.LOGIN_SUCCESS)
        return token_response(pair)


class RefreshView(PublicAPIView):
    permission_classes = [RequiresCustomHeader]

    @extend_schema(
        request=None,
        responses={200: AccessTokenResponseSerializer, 401: DetailSerializer},
        tags=["auth"],
    )
    def post(self, request: Request) -> Response:
        """Rotate the refresh cookie and return a new access token.

        Requires the `X-Requested-With: XMLHttpRequest` header.
        """
        try:
            pair = authentication.rotate_refresh_token(
                get_refresh_cookie(request), request._request
            )
        except authentication.InvalidRefreshToken:
            response = detail_response("Invalid refresh token.", status.HTTP_401_UNAUTHORIZED)
            clear_refresh_cookie(response)
            return response
        return token_response(pair)


class LogoutView(PublicAPIView):
    permission_classes = [RequiresCustomHeader]

    @extend_schema(request=None, responses={204: None}, tags=["auth"])
    def post(self, request: Request) -> Response:
        """Blacklist the refresh cookie and delete it.

        Works with an expired access token. Requires `X-Requested-With`.
        """
        authentication.logout(get_refresh_cookie(request), request._request)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_refresh_cookie(response)
        return response


class PasswordResetRequestView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=EmailSerializer, responses={200: DetailSerializer}, tags=["auth"])
    def post(self, request: Request) -> Response:
        """Send a password reset link (valid 1 h). Always the same response."""
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        passwords.request_password_reset(serializer.validated_data["email"], request._request)
        return detail_response(RESET_MESSAGE)


class PasswordResetConfirmView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(
        request=PasswordResetConfirmSerializer,
        responses={200: DetailSerializer, 400: DetailSerializer},
        tags=["auth"],
    )
    def post(self, request: Request) -> Response:
        """Set a new password; signs the user out everywhere."""
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = passwords.get_reset_user(data["uid"], data["token"])
        if user is None:
            return detail_response(INVALID_LINK_MESSAGE, status.HTTP_400_BAD_REQUEST)
        validate_password_policy(data["new_password"], user, field="new_password")
        passwords.confirm_password_reset(user, data["new_password"], request._request)
        return detail_response("Password has been reset.")


class MeView(APIView):
    # Reachable before onboarding / MFA enrollment (no setup gate).
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: MeSerializer}, tags=["auth"])
    def get(self, request: Request) -> Response:
        """The authenticated user."""
        return Response(MeSerializer(request.user).data)

