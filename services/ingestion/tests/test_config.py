from pathlib import Path

import pytest

from ingestion.config import Settings

REQUIRED = {
    'MONGODB_URI': 'mongodb://user:pw@mongo:27017/documents',
    'DATABASE_URL': 'postgresql://portal:pw@db:5432/tenant_portal',
}


def test_defaults(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    for name in ('MONGODB_DB', 'MEDIA_ROOT', 'POLL_INTERVAL_SECONDS'):
        monkeypatch.delenv(name, raising=False)

    s = Settings.from_env()

    assert s.mongodb_db == 'documents'
    assert s.media_root == Path('/data/media')
    assert s.poll_interval == 5.0


@pytest.mark.parametrize('missing', sorted(REQUIRED))
def test_missing_required_setting_fails(monkeypatch, missing):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(missing, '  ')

    with pytest.raises(RuntimeError, match=missing):
        Settings.from_env()
