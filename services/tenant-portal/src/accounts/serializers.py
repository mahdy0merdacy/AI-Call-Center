"""Input validation and output shapes for the accounts API."""
#validates input 
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from accounts.models import Tenant, User, normalize_email_address

# --- Inputs -----------------------------------------------------------------


class RegisterSerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=200, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)

    def validate(self, attrs: dict) -> dict:
        attrs["email"] = normalize_email_address(attrs["email"])
        # Unsaved user so UserAttributeSimilarityValidator can compare fields.
        candidate = User(
            email=attrs["email"], first_name=attrs["first_name"], last_name=attrs["last_name"]
        )
        validate_password_policy(attrs["password"], candidate)
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class UidTokenSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=64)
    token = serializers.CharField(max_length=128)


class PasswordResetConfirmSerializer(UidTokenSerializer):
    new_password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)


class MFACodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)


class MFAVerifySerializer(MFACodeSerializer):
    mfa_token = serializers.CharField(max_length=512)


class OnboardingSerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=200, trim_whitespace=True)


def validate_password_policy(password: str, user: User, field: str = "password") -> None:
    """Run AUTH_PASSWORD_VALIDATORS, reporting errors under `field`."""
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({field: list(exc.messages)}) from exc


# --- Outputs ----------------------------------------------------------------


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "slug", "is_active", "created_at", "updated_at"]
        read_only_fields = fields


class TenantSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "slug"]
        read_only_fields = fields


class MeSerializer(serializers.ModelSerializer):
    tenant = TenantSummarySerializer(read_only=True, allow_null=True)
    email_verified = serializers.BooleanField(source="is_email_verified", read_only=True)
    mfa_enabled = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "tenant",
            "email_verified",
            "mfa_enabled",
        ]
        read_only_fields = fields


class AccessTokenResponseSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    token_type = serializers.CharField(default="Bearer")
    expires_in = serializers.IntegerField(help_text="Access token lifetime in seconds.")


class MFARequiredResponseSerializer(serializers.Serializer):
    mfa_required = serializers.BooleanField(default=True)
    mfa_token = serializers.CharField()


class TOTPSetupResponseSerializer(serializers.Serializer):
    otpauth_uri = serializers.CharField()
    qr_code_png_base64 = serializers.CharField()


class BackupCodesResponseSerializer(serializers.Serializer):
    backup_codes = serializers.ListField(child=serializers.CharField())


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()
