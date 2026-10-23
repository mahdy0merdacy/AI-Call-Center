from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    """Liveness/readiness probe (spec NFR-03).

    Deliberately does no database query, so a slow or down database never makes
    the orchestrator restart a healthy process. If a readiness check that
    includes the database is needed later, add it as a separate endpoint.
    """
    return JsonResponse({'status': 'ok'})
