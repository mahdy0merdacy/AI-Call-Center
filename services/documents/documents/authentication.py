import logging
import uuid

import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

logger = logging.getLogger(__name__)

# Fixed on purpose, not a setting: accepting only RS256 rules out "algorithm
# confusion" attacks (e.g. a token signed with HS256 using the public key as secret).
ALGORITHMS = ['RS256']


class TenantPrincipal:
    """Who is calling: the tenant named inside a verified token.

    django.contrib.auth is not installed, so there is no User model; DRF only needs
    an object whose `is_authenticated` is true.
    """

    is_authenticated = True

    def __init__(self, tenant_id):
        self.tenant_id = tenant_id


class JWTAuthentication(BaseAuthentication):
    """Authenticate `Authorization: Bearer <jwt>` tokens issued by the auth service.

    The token is signed by the auth service (RS256, private key); we only hold its
    public key, so this service cannot mint tokens. A token is accepted only if the
    signature is valid, it has not expired, issuer/audience match (when configured)
    and it carries a tenant id in the configured claim. The tenant always comes from
    the token, never from the request.

    Anything wrong -> 401. If JWT_PUBLIC_KEY is not configured every request is
    refused (fail closed) rather than letting anything through.
    """

    def authenticate_header(self, request):
        # Makes DRF answer 401 (with this header) instead of 403 on auth failure.
        return 'Bearer'

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b'bearer':
            return None  # no credentials offered; the permission check turns this into 401
        if len(parts) != 2:
            raise AuthenticationFailed('Invalid Authorization header.')

        if not settings.JWT_PUBLIC_KEY:
            logger.error('JWT_PUBLIC_KEY is not configured; refusing all requests.')
            raise AuthenticationFailed('Authentication is not configured.')

        claims = self._decode(parts[1].decode())
        self._check_role(claims)
        return TenantPrincipal(self._tenant_id(claims)), claims

    def _decode(self, token):
        issuer = settings.JWT_ISSUER or None
        audience = settings.JWT_AUDIENCE or None
        required = ['exp'] + (['iss'] if issuer else []) + (['aud'] if audience else [])
        try:
            return jwt.decode(
                token,
                settings.JWT_PUBLIC_KEY,
                algorithms=ALGORITHMS,
                issuer=issuer,
                audience=audience,
                options={'require': required},
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired.') from None
        except jwt.PyJWTError:
            # Bad signature, wrong issuer/audience, malformed, missing claim, ...
            # One generic message: don't tell an attacker which check failed.
            raise AuthenticationFailed('Invalid token.') from None

    def _check_role(self, claims):
        # A valid token with the wrong role (e.g. a platform admin) is a known caller who
        # is not allowed here: 403, not 401. Runs before the tenant check because
        # platform admins have no tenant.
        allowed = settings.JWT_ALLOWED_ROLES
        if allowed and claims.get(settings.JWT_ROLE_CLAIM) not in allowed:
            raise PermissionDenied('This account role cannot use the documents service.')

    def _tenant_id(self, claims):
        raw = claims.get(settings.JWT_TENANT_CLAIM)
        try:
            return uuid.UUID(str(raw))
        except (ValueError, TypeError):
            raise AuthenticationFailed('Token does not carry a valid tenant.') from None
