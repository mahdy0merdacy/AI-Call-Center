"""Request-level middleware: correlation ids and structured access logs."""
# Request ID and security middleware
#that runs on every request
import logging
import re
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from config.logging import correlation_id_var

logger = logging.getLogger("portal.request")

CORRELATION_HEADER = "X-Correlation-ID"
# Accept caller-provided ids only if they are short and log-safe.
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class CorrelationIdMiddleware:
    """Propagate `X-Correlation-ID` (or generate one) and log each request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(CORRELATION_HEADER, "")
        correlation_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        request.correlation_id = correlation_id
        token = correlation_id_var.set(correlation_id)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            response[CORRELATION_HEADER] = correlation_id
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                },
            )
            return response
        finally:
            correlation_id_var.reset(token)
