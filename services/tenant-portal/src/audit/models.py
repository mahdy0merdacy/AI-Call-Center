"""Authentication audit trail.

Never store passwords, tokens, MFA codes or submitted emails in this table.
"""

import uuid

from django.conf import settings
from django.db import models


class AuthEvent(models.Model):
    class EventType(models.TextChoices):
        REGISTER = "register"
        EMAIL_VERIFIED = "email_verified"
        LOGIN_SUCCESS = "login_success"
        LOGIN_FAILED = "login_failed"
        ACCOUNT_LOCKED = "account_locked"
        LOGOUT = "logout"
        TOKEN_REFRESH = "token_refresh"
        PASSWORD_RESET_REQUESTED = "password_reset_requested"
        PASSWORD_RESET_DONE = "password_reset_done"
        MFA_ENROLLED = "mfa_enrolled"
        MFA_SUCCESS = "mfa_success"
        MFA_FAILED = "mfa_failed"
        GOOGLE_LOGIN = "google_login"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auth_events",
    )
    tenant = models.ForeignKey(
        "accounts.Tenant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auth_events",
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["event_type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
