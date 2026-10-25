"""Settings shared by every environment. All secrets come from the environment."""

from datetime import timedelta
from pathlib import Path

import environ
from corsheaders.defaults import default_headers
from csp.constants import NONE, SELF
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()


def read_key_file(path: str) -> str:
    """Read a PEM key from disk, failing loudly if it is missing."""
    try:
        return Path(path).read_text()
    except OSError as exc:
        raise ImproperlyConfigured(
            f"Cannot read key file {path!r}. Run scripts/generate_keys.sh "
            "or set JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH."
        ) from exc


# --- Core -------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "csp",
    "drf_spectacular",
    "axes",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.headless",
    # Local
    "accounts",
    "audit",
]

MIDDLEWARE = [
    "config.middleware.CorrelationIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # Must stay last (django-axes requirement).
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# Database cache: shared by all gunicorn workers / pods without adding Redis.
# Used for DRF throttling. Table is created by `manage.py createcachetable`.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Number of trusted reverse proxies; drives client IP resolution everywhere
# (DRF throttling, django-axes, audit log). 0 = trust REMOTE_ADDR only.
NUM_PROXIES = env.int("NUM_PROXIES", default=0)

FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000").rstrip("/")

# --- Authentication ---------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    # Must be first: rejects locked-out credentials before other backends run.
    "axes.backends.AxesStandaloneBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    # Fallbacks only verify legacy hashes; new hashes always use Argon2.
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

EMAIL_VERIFICATION_TIMEOUT = int(timedelta(hours=24).total_seconds())
PASSWORD_RESET_TIMEOUT = int(timedelta(hours=1).total_seconds())

LOGIN_LOCKOUT_THRESHOLD = 5
LOGIN_LOCKOUT_DURATION = timedelta(minutes=15)

# Header required on cookie-authenticated endpoints (refresh, logout, Google
# exchange). Browsers cannot send it cross-origin without a CORS preflight.
AUTH_CUSTOM_HEADER = "X-Requested-With"
AUTH_CUSTOM_HEADER_VALUE = "XMLHttpRequest"

# --- JWT (simplejwt, RS256) -------------------------------------------------

JWT_PRIVATE_KEY_PATH = env("JWT_PRIVATE_KEY_PATH", default="/keys/jwt_private.pem")
JWT_PUBLIC_KEY_PATH = env("JWT_PUBLIC_KEY_PATH", default="/keys/jwt_public.pem")

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "RS256",
    "SIGNING_KEY": read_key_file(JWT_PRIVATE_KEY_PATH),
    "VERIFYING_KEY": read_key_file(JWT_PUBLIC_KEY_PATH),
    "ISSUER": env("JWT_ISSUER", default="tenant-portal"),
    "AUDIENCE": env("JWT_AUDIENCE", default="ai-voice-platform"),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "sub",
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_TOKEN_CLASSES": ("accounts.jwt.PortalAccessToken",),
}

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth/"
REFRESH_COOKIE_SAMESITE = "Strict"
REFRESH_COOKIE_SECURE = env.bool("REFRESH_COOKIE_SECURE", default=True)
REFRESH_COOKIE_DOMAIN = env("REFRESH_COOKIE_DOMAIN", default=None)

# --- MFA (django-otp) -------------------------------------------------------

MFA_ENCRYPTION_KEY = env("MFA_ENCRYPTION_KEY")
MFA_REQUIRED_FOR_PLATFORM_ADMIN = env.bool("MFA_REQUIRED_FOR_PLATFORM_ADMIN", default=True)
MFA_TOKEN_TTL = timedelta(minutes=5)
MFA_MAX_ATTEMPTS = 5
MFA_BACKUP_CODE_COUNT = 10
OTP_TOTP_ISSUER = "AIVoice"

# --- django-axes (second brute-force layer, keyed on IP + username) ---------

AXES_ENABLED = env.bool("AXES_ENABLED", default=True)
# Looser than the per-account lockout above: it catches an attacker who keeps
# retrying across lockout windows from the same IP.
AXES_FAILURE_LIMIT = 10
AXES_COOLOFF_TIME = timedelta(hours=1)
AXES_LOCKOUT_PARAMETERS = [["ip_address", "username"]]
AXES_RESET_ON_SUCCESS = True
AXES_CLIENT_IP_CALLABLE = "audit.utils.get_client_ip"
AXES_LOCKOUT_CALLABLE = "accounts.services.authentication.axes_lockout_response"
# Axes' own log lines include the raw username (an email); keep them quiet.
AXES_VERBOSE = False

# --- django-allauth (headless, Google only) ---------------------------------

HEADLESS_ONLY = True
HEADLESS_CLIENTS = ("browser",)
HEADLESS_FRONTEND_URLS = {
    "socialaccount_login_error": f"{FRONTEND_URL}/auth/google/callback",
}
# Local email/password accounts are handled by our own API, not allauth.
SOCIALACCOUNT_ONLY = True
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_ADAPTER = "accounts.adapters.AccountAdapter"
SOCIALACCOUNT_ADAPTER = "accounts.adapters.SocialAccountAdapter"
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"  # we require Google's email_verified
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False  # never match accounts by email
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APPS": [
            {
                "client_id": env("GOOGLE_CLIENT_ID", default=""),
                "secret": env("GOOGLE_CLIENT_SECRET", default=""),
                "key": "",
            }
        ],
        "SCOPE": ["openid", "email", "profile"],
        "AUTH_PARAMS": {"prompt": "select_account"},
        "OAUTH_PKCE_ENABLED": True,
        "FETCH_USERINFO": False,  # use the verified ID token claims
    }
}
# Max age of the allauth login we accept at the token exchange endpoint.
GOOGLE_EXCHANGE_MAX_AGE = timedelta(minutes=5)

# The session is only used during the Google OAuth handshake.
SESSION_COOKIE_AGE = int(timedelta(minutes=15).total_seconds())
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"  # must survive the top-level redirect from Google

# --- Django REST framework --------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.PortalJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "accounts.permissions.AccountSetupComplete",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {
        "register": "5/hour",
        "login": "10/minute",
        "password_reset": "5/hour",
        "mfa_verify": "10/minute",
        "resend_verification": "3/hour",
    },
    "NUM_PROXIES": NUM_PROXIES,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Tenant Portal API",
    "DESCRIPTION": "Authentication and tenant management for the AI Voice Reception Platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
    "COMPONENT_SPLIT_REQUEST": True,
}
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=True)

# --- Email ------------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@aivoice.local")

# --- Security headers -------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
CSRF_COOKIE_SAMESITE = "Lax"

# The API only returns JSON, so the default policy forbids everything.
# The Swagger UI view relaxes it locally (see config/urls.py).
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": [NONE],
        "base-uri": [NONE],
        "frame-ancestors": [NONE],
        "form-action": [SELF],
    }
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True  # needed for the refresh cookie
CORS_ALLOW_HEADERS = (*default_headers, "x-correlation-id")
CORS_EXPOSE_HEADERS = ["X-Correlation-ID"]
# allauth's browser flow (form POST to start Google login) needs CSRF from the
# frontend origin; allauth also uses this list to validate callback URLs.
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# --- Logging (JSON lines with correlation id) -------------------------------

LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"correlation_id": {"()": "config.logging.CorrelationIdFilter"}},
    "formatters": {"json": {"()": "config.logging.JSONFormatter"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["correlation_id"],
        }
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        # Replaced by our own structured request log (config.middleware).
        "django.server": {"level": "WARNING"},
        "axes": {"level": "ERROR"},
    },
}
