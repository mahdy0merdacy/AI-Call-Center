"""Infrastructure endpoints: health probes and the JWKS document."""

from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from accounts.jwt import public_jwk


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Readiness probe: 200 only if the database answers."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "unhealthy", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})


@require_GET
def liveness(request: HttpRequest) -> JsonResponse:
    """Liveness probe: the process is up (no dependency checks)."""
    return JsonResponse({"status": "ok"})


@require_GET
def jwks(request: HttpRequest) -> JsonResponse:
    """Public signing keys, for other services to verify portal JWTs."""
    response = JsonResponse({"keys": [public_jwk()]})
    response["Cache-Control"] = "public, max-age=3600"
    return response
