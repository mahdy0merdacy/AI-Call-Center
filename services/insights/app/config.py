"""Settings, read once from the environment at start-up."""

import os
from dataclasses import dataclass

DEFAULT_ISSUER = "tenant-portal"
DEFAULT_AUDIENCE = "ai-voice-platform"


@dataclass(frozen=True)
class Settings:
    database_url: str
    # PEM text. Empty means every request is refused (fail closed), never let through.
    jwt_public_key: str
    jwt_issuer: str = DEFAULT_ISSUER
    jwt_audience: str = DEFAULT_AUDIENCE
    # Bounds every query on the server and every wait for a pooled connection.
    query_timeout_s: float = 3.0
    pool_max_size: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("ANALYTICS_DATABASE_URL", "")
        if not url:
            raise RuntimeError("ANALYTICS_DATABASE_URL is not set")
        return cls(
            database_url=url,
            # Secrets and .env files often carry the PEM on one line with literal "\n".
            jwt_public_key=os.environ.get("JWT_PUBLIC_KEY", "").replace("\\n", "\n").strip(),
            # Always checked: an empty value falls back to the default instead of
            # switching the check off.
            jwt_issuer=os.environ.get("JWT_ISSUER") or DEFAULT_ISSUER,
            jwt_audience=os.environ.get("JWT_AUDIENCE") or DEFAULT_AUDIENCE,
            query_timeout_s=float(os.environ.get("QUERY_TIMEOUT_S", "3")),
            pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "4")),
        )
