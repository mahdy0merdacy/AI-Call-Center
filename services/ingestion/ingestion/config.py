"""Settings, read once from the environment. No secrets have defaults."""

import os
from dataclasses import dataclass
from pathlib import Path


def _require(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f'{name} must be set (see .env.example)')
    return value


@dataclass(frozen=True)
class Settings:
    # Documents service database: where uploads and their ingestion_status live.
    mongodb_uri: str
    mongodb_db: str
    # tenant-portal Postgres, which holds the document_chunks table (pgvector).
    database_url: str
    # Same volume the documents service writes uploads to, mounted read-only.
    media_root: Path
    # Seconds between two looks for pending documents.
    poll_interval: float

    @classmethod
    def from_env(cls) -> 'Settings':
        return cls(
            mongodb_uri=_require('MONGODB_URI'),
            mongodb_db=os.environ.get('MONGODB_DB', 'documents'),
            database_url=_require('DATABASE_URL'),
            media_root=Path(os.environ.get('MEDIA_ROOT', '/data/media')),
            poll_interval=float(os.environ.get('POLL_INTERVAL_SECONDS', '5')),
        )
