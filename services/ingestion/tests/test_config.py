from pathlib import Path

import pytest

from ingestion.config import KnowledgeSettings, Settings

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


def test_embedding_url_is_optional_and_trimmed(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('EMBEDDING_URL', raising=False)
    assert Settings.from_env().embedding_url == ''  # load the model here, as before

    monkeypatch.setenv('EMBEDDING_URL', ' http://knowledge:8100/ ')
    assert Settings.from_env().embedding_url == 'http://knowledge:8100'


def test_knowledge_defaults(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', REQUIRED['DATABASE_URL'])
    monkeypatch.delenv('MONGODB_URI', raising=False)  # the knowledge service never uses Mongo
    for name in ('EMBEDDING_MODEL', 'EMBEDDING_THREADS', 'PORT'):
        monkeypatch.delenv(name, raising=False)

    s = KnowledgeSettings.from_env()

    assert (s.embedding_model, s.embedding_threads, s.port) == ('BAAI/bge-m3', 2, 8100)


def test_knowledge_needs_a_database_url(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', ' ')
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        KnowledgeSettings.from_env()
