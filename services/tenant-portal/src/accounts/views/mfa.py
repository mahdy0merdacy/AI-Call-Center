"""TOTP MFA endpoints (platform admins)."""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.permissions import IsPlatformAdminRole
from accounts.serializers import (
    AccessTokenResponseSerializer,
    BackupCodesResponseSerializer,
    DetailSerializer,
    MFACodeSerializer,
    MFAVerifySerializer,
    TOTPSetupResponseSerializer,
)
from accounts.services import mfa
from accounts.services.authentication import complete_login
from accounts.views.common import PublicAPIView, detail_response, token_response
from audit.services import EventType

MFAErrorSerializer = inline_serializer(
    "MFAError", {"detail": serializers.CharField(), "code": serializers.CharField()}
)


def _mfa_error(message: str, code: str) -> Response:
    return Response({"detail": message, "code": code}, status=status.HTTP_401_UNAUTHORIZED)


class TOTPSetupView(APIView):
    # No setup gate: this is how an admin satisfies MFA_REQUIRED_FOR_PLATFORM_ADMIN.
    permission_classes = [IsAuthenticated, IsPlatformAdminRole]

    @extend_schema(
        request=None,
        responses={200: TOTPSetupResponseSerializer, 409: DetailSerializer},
        tags=["mfa"],
    )
    def post(self, request: Request) -> Response:
        """Start TOTP enrollment: returns the otpauth:// URI and a QR code PNG."""
        try:
            enrollment = mfa.start_totp_enrollment(request.user)
        except mfa.AlreadyEnrolled:
            return detail_response("MFA is already enabled.", status.HTTP_409_CONFLICT)
        data = TOTPSetupResponseSerializer(enrollment).data
        return Response(data, headers={"Cache-Control": "no-store"})


class TOTPConfirmView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdminRole]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "mfa_verify"

    @extend_schema(
        request=MFACodeSerializer,
        responses={200: BackupCodesResponseSerializer, 400: DetailSerializer},
        tags=["mfa"],
    )
    def post(self, request: Request) -> Response:
        """Confirm enrollment with a 6-digit code. Returns 10 backup codes, once."""
        serializer = MFACodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            codes = mfa.confirm_totp_enrollment(
                request.user, serializer.validated_data["code"], request._request
            )
        except mfa.NoPendingEnrollment:
            return detail_response("No pending MFA enrollment.", status.HTTP_400_BAD_REQUEST)
        except mfa.InvalidMFACode:
            return detail_response("Invalid code.", status.HTTP_400_BAD_REQUEST)
        return Response({"backup_codes": codes}, headers={"Cache-Control": "no-store"})


class MFAVerifyView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "mfa_verify"

    @extend_schema(
        request=MFAVerifySerializer,
        responses={200: AccessTokenResponseSerializer, 401: MFAErrorSerializer},
        tags=["mfa"],
    )
    def post(self, request: Request) -> Response:
        """Second login step: `mfa_token` + TOTP code or backup code."""
        serializer = MFAVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        django_request = request._request
        try:
            user = mfa.verify_mfa_challenge(data["mfa_token"], data["code"], django_request)
        except mfa.InvalidMFAToken:
            return _mfa_error("MFA session expired; log in again.", "mfa_token_invalid")
        except mfa.InvalidMFACode:
            return _mfa_error("Invalid MFA code.", "mfa_code_invalid")
        return token_response(complete_login(django_request, user, EventType.LOGIN_SUCCESS))
