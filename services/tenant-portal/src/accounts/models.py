"""Tenant and user models, plus MFA storage."""

from __future__ import annotations

import os
import uuid

from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.crypto import decrypt_totp_secret, encrypt_totp_secret, hash_backup_code

TOTP_SECRET_BYTES = 20


class Tenant(models.Model):
    """A client company (e.g. a clinic). All tenant data hangs off this row."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


def normalize_email_address(email: str) -> str:
    """Canonical form used for storage and lookup (case-insensitive emails)."""
    return email.strip().lower()


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **fields) -> User:
        """Create a user. A missing password yields an unusable one."""
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=normalize_email_address(email), **fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **fields) -> User:
        """Create a platform admin: verified email, no tenant, staff."""
        fields.update(
            role=User.Role.PLATFORM_ADMIN,
            tenant=None,
            is_email_verified=True,
            is_staff=True,
            is_superuser=True,
        )
        return self.create_user(email, password, **fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        PLATFORM_ADMIN = "platform_admin", "Platform admin"
        TENANT_ADMIN = "tenant_admin", "Tenant admin"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Null only for platform admins and for Google users before onboarding.
    tenant = models.ForeignKey(
        Tenant, null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.TENANT_ADMIN)
    is_active = models.BooleanField(default=True)
    is_email_verified = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(role="platform_admin", tenant__isnull=False),
                name="user_platform_admin_has_no_tenant",
            ),
            # A tenant_admin without a tenant is only valid while a Google
            # user is onboarding; those users have an unusable password.
            # Email/password registrations always create the tenant atomically.
            models.CheckConstraint(
                condition=~models.Q(role="tenant_admin", tenant__isnull=True)
                | models.Q(password__startswith=UNUSABLE_PASSWORD_PREFIX),
                name="user_tenant_admin_requires_tenant",
            ),
        ]

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        """Model-level validation mirroring the database constraints."""
        super().clean()
        self.email = normalize_email_address(self.email)
        if self.role == self.Role.PLATFORM_ADMIN and self.tenant_id is not None:
            raise ValidationError({"tenant": "Platform admins cannot belong to a tenant."})
        if (
            self.role == self.Role.TENANT_ADMIN
            and self.tenant_id is None
            and self.has_usable_password()
        ):
            raise ValidationError({"tenant": "Tenant admins must belong to a tenant."})

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > timezone.now()

    @property
    def needs_onboarding(self) -> bool:
        return self.role == self.Role.TENANT_ADMIN and self.tenant_id is None

    @property
    def mfa_enabled(self) -> bool:
        return EncryptedTOTPDevice.objects.filter(user=self, confirmed=True).exists()

    def can_authenticate(self) -> bool:
        """Active user whose tenant (if any) is not suspended."""
        if not self.is_active:
            return False
        return self.tenant_id is None or self.tenant.is_active


class MFAChallenge(models.Model):
    """Pending second factor after a successful password check.

    The client only sees a signed reference to this row (the `mfa_token`);
    keeping the attempt counter in the database makes the limit hold across
    workers and pods.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mfa_challenges")
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)


class EncryptedTOTPDevice(TOTPDevice):
    """TOTPDevice whose `key` column holds an AES-SIV ciphertext.

    django-otp reads the secret only through `bin_key`, so overriding it keeps
    all of django-otp's verification, drift and anti-replay (`last_t`) logic.
    """

    class Meta:
        proxy = True

    @property
    def bin_key(self) -> bytes:
        return decrypt_totp_secret(self.key, self.user_id)

    @classmethod
    def create_unconfirmed(cls, user: User) -> EncryptedTOTPDevice:
        """Create a new device with a fresh random secret."""
        device = cls(user=user, name="Authenticator app", confirmed=False)
        device.key = encrypt_totp_secret(os.urandom(TOTP_SECRET_BYTES), user.pk)
        device.save()
        return device


class BackupCodeDevice(StaticDevice):
    """StaticDevice storing backup codes as keyed hashes (single use)."""

    class Meta:
        proxy = True

    @staticmethod
    def normalize(code: str) -> str:
        return code.replace("-", "").replace(" ", "").lower()

    def set_codes(self, codes: list[str]) -> None:
        """Replace all backup codes with the given plaintext codes."""
        with transaction.atomic():
            self.token_set.all().delete()
            StaticToken.objects.bulk_create(
                StaticToken(device=self, token=hash_backup_code(self.normalize(c)))
                for c in codes
            )

    def verify_token(self, token: str) -> bool:
        """Consume a backup code. The DELETE makes concurrent reuse impossible."""
        verify_allowed, _ = self.verify_is_allowed()
        if not verify_allowed:
            return False
        digest = hash_backup_code(self.normalize(token))
        deleted, _ = self.token_set.filter(token=digest).delete()
        if deleted:
            self.throttle_reset(commit=False)
            self.set_last_used_timestamp(commit=False)
            self.save()
            return True
        self.throttle_increment()
        return False
