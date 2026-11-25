"""Request helpers shared by audit logging and django-axes."""

import ipaddress

from django.conf import settings
from django.http import HttpRequest

USER_AGENT_MAX_LENGTH = 512


def get_client_ip(request: HttpRequest) -> str | None:
    """Return the client IP, trusting X-Forwarded-For only for NUM_PROXIES hops.

    Uses the same rule as DRF's throttling (`NUM_PROXIES`), so throttling,
    lockout and audit logs all agree on who the client is.
    """
    candidate = request.META.get("REMOTE_ADDR")
    num_proxies = settings.NUM_PROXIES
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if num_proxies > 0 and forwarded:
        hops = [hop.strip() for hop in forwarded.split(",")]
        if len(hops) >= num_proxies:
            candidate = hops[-num_proxies]
    try:
        return str(ipaddress.ip_address(candidate)) if candidate else None
    except ValueError:
        return None


def get_user_agent(request: HttpRequest) -> str:
    """Return the (truncated) User-Agent header."""
    return request.META.get("HTTP_USER_AGENT", "")[:USER_AGENT_MAX_LENGTH]
