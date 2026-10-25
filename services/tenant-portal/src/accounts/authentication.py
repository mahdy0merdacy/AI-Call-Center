"""DRF authentication: simplejwt plus account/tenant status checks."""

from django.core.exceptions import ValidationError
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import Token

from accounts.models import User


class PortalJWTAuthentication(JWTAuthentication):
    """Validate the Bearer token, then re-check the user on every request.

    A suspended tenant or deactivated user loses access immediately rather
    than when the (15 minute) access token expires.
    """

    def get_user(self, validated_token: Token) -> User:
        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
        except KeyError as exc:
            raise InvalidToken("Token contained no recognizable user identification") from exc
        try:
            user = User.objects.select_related("tenant").get(pk=user_id)
        except (User.DoesNotExist, ValidationError) as exc:
            raise AuthenticationFailed("User not found", code="user_not_found") from exc
        if not user.can_authenticate():
            raise AuthenticationFailed("User is inactive", code="user_inactive")
        return user
