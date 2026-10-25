"""Write authentication events to the audit table and the structured log."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.http import HttpRequest

from audit.models import AuthEvent
from audit.utils import get_client_ip, get_user_agent

if TYPE_CHECKING:
    from accounts.models import Tenant, User

logger = logging.getLogger("portal.audit")

EventType = AuthEvent.EventType


def log_auth_event(
    event_type: str,
    *,
    request: HttpRequest | None = None,
    user: User | None = None,
    tenant: Tenant | None = None,
) -> AuthEvent:
    """Record an authentication event.

    The tenant defaults to the user's tenant. Only ids, IP and user agent are
    stored; callers must never pass credentials or codes.
    """
    tenant_id = tenant.pk if tenant is not None else getattr(user, "tenant_id", None)
    event = AuthEvent.objects.create(
        event_type=event_type,
        user=user,
        tenant_id=tenant_id,
        ip_address=get_client_ip(request) if request is not None else None,
        user_agent=get_user_agent(request) if request is not None else "",
    )
    logger.info(
        "auth event",
        extra={
            "event_type": event_type,
            "user_id": str(user.pk) if user is not None else None,
            "tenant_id": str(tenant_id) if tenant_id else None,
        },
    )
    return event
