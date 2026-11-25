"""The worker's choice of embedder, and who is to blame when embedding fails."""

import threading
from pathlib import Path

import pytest

from ingestion import worker
from ingestion.config import Settings
from ingestion.remote_embedding import EmbeddingUnavailable

DOC = {'id': '5c3351a8e26a4d1586c9f5960048c462', 'tenant_id': 'cfc05ea13de64b6ca96347343cb10651'}


def settings(embedding_url=''):
    return Settings(
        mongodb_uri='mongodb://unused',
        mongodb_db='documents',
        database_url='postgresql://unused',
        media_root=Path('/data/media'),
        poll_interval=5.0,
        embedding_model='BAAI/bge-m3',
        embedding_threads=2,
        stuck_after_minutes=10,
        embedding_url=embedding_url,
    )


BUILT = []  # (class name, args, kwargs) of every embedder made


class Recorder:
    """Stands in for Embedder / RemoteEmbedder: records how it was built."""

    def __init__(self, *args, **kwargs):
        BUILT.append((type(self).__name__, args, kwargs))


class Local(Recorder):
    pass


class Remote(Recorder):
    pass


@pytest.fixture
def fakes(monkeypatch):
    BUILT.clear()
    monkeypatch.setattr(worker, 'Embedder', Local)
    monkeypatch.setattr(worker, 'RemoteEmbedder', Remote)
    return BUILT


def test_without_embedding_url_the_model_is_loaded_here(fakes):
    worker.make_embedder(settings(), threading.Event())
    assert fakes == [('Local', ('BAAI/bge-m3',), {'threads': 2})]


def test_with_embedding_url_the_knowledge_service_embeds(fakes):
    stop = threading.Event()
    worker.make_embedder(settings('http://knowledge:8100'), stop)
    assert fakes == [('Remote', ('http://knowledge:8100', 'BAAI/bge-m3'), {'stop': stop})]


class FailingEmbedder:
    model_name = 'BAAI/bge-m3'

    def __init__(self, error):
        self.error = error

    def embed(self, texts):
        raise self.error


@pytest.fixture
def statuses(monkeypatch):
    """Final statuses written to Mongo, instead of a real collection."""
    written = []
    monkeypatch.setattr(
        worker,
        'read_document_file',
        lambda doc, root: ('policy.txt', b'Stock records are kept for ten years in the archive.'),
    )
    monkeypatch.setattr(worker.mongo, 'mark_failed', lambda c, d, msg: written.append(msg))
    monkeypatch.setattr(worker.mongo, 'mark_completed', lambda c, d: written.append('completed'))
    return written


def test_knowledge_service_down_is_not_the_documents_fault(statuses):
    embedder = FailingEmbedder(EmbeddingUnavailable('knowledge service unavailable'))
    # Raised to the main loop, which puts the document back to pending.
    with pytest.raises(EmbeddingUnavailable):
        worker.process_one(DOC, None, None, embedder, settings('http://knowledge:8100'))
    assert statuses == []  # not marked failed


def test_any_other_embedding_error_still_fails_the_document(statuses):
    embedder = FailingEmbedder(RuntimeError('knowledge service answered HTTP 500'))
    worker.process_one(DOC, None, None, embedder, settings('http://knowledge:8100'))
    assert statuses == ['Processing failed (RuntimeError).']
