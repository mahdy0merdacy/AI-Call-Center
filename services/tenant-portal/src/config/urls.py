from csp.constants import SELF, UNSAFE_INLINE
from csp.decorators import csp_update
from django.conf import settings
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView

from accounts import urls as accounts_urls
from config import views

SWAGGER_CDN = "https://cdn.jsdelivr.net"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("health/live/", views.liveness, name="liveness"),
    path(".well-known/jwks.json", views.jwks, name="jwks"),
    path("api/auth/", include((accounts_urls.auth_urlpatterns, "auth"))),
    path("api/admin/", include((accounts_urls.admin_urlpatterns, "platform_admin"))),
    path("api/tenant/", include((accounts_urls.tenant_urlpatterns, "tenant"))),
    # allauth: Google OAuth callback + headless browser API.
    path("accounts/", include("allauth.urls")),
    path("_allauth/", include("allauth.headless.urls")),
]

if settings.API_DOCS_ENABLED:
    # Swagger UI loads from a CDN and uses an inline <style>; relax CSP here only.
    docs_view = csp_update(
        {
            "script-src": [SELF, SWAGGER_CDN],
            "style-src": [SELF, SWAGGER_CDN, UNSAFE_INLINE],
            "img-src": [SELF, "data:", SWAGGER_CDN],
            "connect-src": [SELF],
        }
    )(SpectacularSwaggerSplitView.as_view(url_name="schema"))
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", docs_view, name="api-docs"),
    ]
