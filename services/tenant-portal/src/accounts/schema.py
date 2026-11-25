"""OpenAPI (Swagger) description of our JWT authentication."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class PortalJWTScheme(OpenApiAuthenticationExtension):
    """Bearer JWT, so Swagger UI shows the Authorize button."""

    target_class = "accounts.authentication.PortalJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
