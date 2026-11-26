"""Knowledge API with a fake model and a fake store: no torch, no Postgres."""

import logging
import threading
import time
import uuid
import zlib

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from ingestion import api
from ingestion.api import BULK, BULK_SLICE, SEARCH, Model, create_app
from ingestion.config import KnowledgeSettings
from ingestion.embedding import DIMENSIONS
from ingestion.search import Hit

MODEL = 'BAAI/bge-m3'
SETTINGS = KnowledgeSettings(
    database_url='postgresql://unused', embedding_model=MODEL, embedding_threads=1, port=0
)
TENANT = 'cfc05ea1-3de6-4b6c-a963-47343cb10651'


def vector_for(text: str) -> np.ndarray:
    """A different unit vector for every text, like the real model gives."""
    rng = np.random.default_rng(zlib.crc32(text.encode()))
    vector = rng.standard_normal(DIMENSIONS).astype(np.float32)
    return vector / np.linalg.norm(vector)


class FakeEmbedder:
    model_name = MODEL

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return np.stack([vector_for(t) for t in texts])


class FakeStore:
    def __init__(self, found=()):
        self.found = list(found)
        self.error = None
        self.searches = []
        self.opened = self.closed = False

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def ping(self):
        if self.error:
            raise self.error

    def search(self, tenant_id, model, vector, query, k):
        self.searches.append((tenant_id, model, vector, query, k))
        if self.error:
            raise self.error
        return self.found


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, 'timed out'
        time.sleep(0.01)


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(embedder, store):
    with TestClient(create_app(SETTINGS, load_embedder=lambda: embedder, store=store)) as c:
        wait_until(lambda: c.get('/healthz').status_code == 200)
        yield c


def search_body(**overrides):
    return {'tenant_id': TENANT, 'query': 'how long are stock records kept?', 'k': 2, **overrides}


# ---------------------------------------------------------------- probes


def test_probes_before_and_after_the_model_is_loaded(store):
    release = threading.Event()

    def slow_load():
        release.wait(5)
        return FakeEmbedder()

    with TestClient(create_app(SETTINGS, load_embedder=slow_load, store=store)) as client:
        # Alive at once, but not ready: nothing must be routed here yet.
        assert client.get('/livez').json() == {'status': 'alive'}
        health = client.get('/healthz')
        assert health.status_code == 503
        assert health.json()['status'] == 'starting'
        assert client.post('/v1/embed', json={'texts': ['a']}).status_code == 503
        assert client.post('/v1/search', json=search_body()).status_code == 503

        release.set()
        wait_until(lambda: client.get('/healthz').status_code == 200)
        assert client.get('/healthz').json() == {'status': 'ok', 'model': MODEL, 'dimensions': 1024}
    assert store.opened and store.closed


def test_healthz_is_503_while_the_database_does_not_answer(client, store):
    store.error = psycopg.OperationalError('connection refused')
    health = client.get('/healthz')
    assert health.status_code == 503
    assert health.json() == {
        'status': 'unavailable',
        'detail': 'database unavailable (OperationalError)',
    }
    assert client.get('/livez').status_code == 200  # alive: no restart for that

    store.error = None
    assert client.get('/healthz').status_code == 200


def test_healthz_is_503_when_grants_are_missing(client, store):
    store.error = psycopg.errors.InsufficientPrivilege('permission denied')
    assert client.get('/healthz').json()['detail'] == (
        'database unavailable (InsufficientPrivilege)'
    )


def test_a_failed_model_load_is_shown_then_retried(monkeypatch, store):
    monkeypatch.setattr(api, 'LOAD_RETRY_FIRST', 0.01)
    attempts = []
    second_try = threading.Event()

    def flaky_load():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError('download cut')
        second_try.wait(5)
        return FakeEmbedder()

    with TestClient(create_app(SETTINGS, load_embedder=flaky_load, store=store)) as client:
        wait_until(lambda: len(attempts) == 2)
        health = client.get('/healthz')
        assert health.status_code == 503
        assert health.json() == {
            'status': 'unavailable',
            'detail': 'embedding model failed to load (OSError), retrying',
        }
        second_try.set()
        wait_until(lambda: client.get('/healthz').status_code == 200)


def test_probe_lines_are_kept_out_of_the_access_log():
    def record(path):
        args = ('10.0.0.1:5000', 'GET', path, '1.1', 200)
        return logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s', args, None)

    assert not api._not_a_probe(record('/healthz'))
    assert not api._not_a_probe(record('/livez'))
    assert api._not_a_probe(record('/v1/search'))


# ---------------------------------------------------------------- /v1/embed


def test_embed_returns_one_exact_vector_per_text_in_order(client):
    texts = [f'chunk number {i}' for i in range(10)]
    response = client.post('/v1/embed', json={'texts': texts})

    assert response.status_code == 200
    body = response.json()
    assert body['model'] == MODEL
    assert body['dimensions'] == 1024
    vectors = np.asarray(body['vectors'], dtype=np.float32)
    assert vectors.shape == (10, 1024)
    # Through JSON and back, the float32 values are exactly the model's.
    assert np.array_equal(vectors, np.stack([vector_for(t) for t in texts]))


def test_embed_encodes_bulk_requests_in_small_slices(client, embedder):
    texts = [f't{i}' for i in range(10)]
    client.post('/v1/embed', json={'texts': texts})
    slices = [texts[i : i + BULK_SLICE] for i in range(0, len(texts), BULK_SLICE)]
    assert len(slices) > 1
    assert embedder.calls[-len(slices) :] == slices


def test_embed_accepts_the_limits(client):
    assert client.post('/v1/embed', json={'texts': ['x'] * 64}).status_code == 200
    assert client.post('/v1/embed', json={'texts': ['x' * 8000]}).status_code == 200


@pytest.mark.parametrize(
    'payload',
    [
        {'texts': []},
        {'texts': ['x'] * 65},
        {'texts': ['']},
        {'texts': ['x' * 8001]},
        {'texts': 'one text'},
        {'texts': [1]},
        {},
    ],
    ids=['none', 'too-many', 'empty-text', 'too-long', 'not-a-list', 'not-text', 'missing'],
)
def test_embed_rejects_bad_input_with_400(client, embedder, payload):
    calls_before = len(embedder.calls)
    response = client.post('/v1/embed', json=payload)
    assert response.status_code == 400
    assert embedder.calls[calls_before:] == []  # the model never sees it
    assert 'x' * 100 not in response.text  # the input is not echoed back


def test_invalid_json_is_a_400(client):
    response = client.post(
        '/v1/embed', content=b'{"texts": [', headers={'content-type': 'application/json'}
    )
    assert response.status_code == 400


# ---------------------------------------------------------------- /v1/search


def test_search_returns_the_fused_chunks_of_the_requested_tenant(client, store):
    doc_1, doc_2 = str(uuid.uuid4()), str(uuid.uuid4())
    store.found = [
        (Hit(7, doc_1, 'Stock Policy', '5. Records', 'Kept 10 y.', 0.71), 0.032),
        (Hit(3, doc_2, None, None, 'Audits yearly.'), 0.016),   # words ranking only
    ]
    response = client.post('/v1/search', json=search_body(query='  stock records  '))

    assert response.status_code == 200
    body = response.json()
    assert body['chunks'] == [
        {
            'document_id': doc_1,
            'document_title': 'Stock Policy',
            'section': '5. Records',
            'content': 'Kept 10 y.',
            'score': 0.032,
            'similarity': 0.71,
        },
        {
            'document_id': doc_2,
            'document_title': None,
            'section': None,
            'content': 'Audits yearly.',
            'score': 0.016,
            'similarity': None,
        },
    ]
    assert body['best_similarity'] == 0.71      # "do the documents cover this at all"
    assert isinstance(body['took_ms'], int) and body['took_ms'] >= 0
    tenant_id, model, vector, query, k = store.searches[-1]
    assert tenant_id == uuid.UUID(TENANT)
    assert model == MODEL  # only vectors of the loaded model are compared
    assert query == 'stock records'  # trimmed
    assert k == 2
    assert np.array_equal(vector, vector_for('stock records'))


def test_search_defaults_to_4_chunks(client, store):
    body = search_body()
    del body['k']
    assert client.post('/v1/search', json=body).status_code == 200
    assert store.searches[-1][4] == 4


@pytest.mark.parametrize(
    'override',
    [
        {'tenant_id': 'acme'},
        {'tenant_id': ''},
        {'tenant_id': 'cfc05ea1-3de6-4b6c-a963'},
        {'tenant_id': None},
        {'query': ''},
        {'query': '   '},
        {'query': 'x' * 2001},
        {'k': 0},
        {'k': 11},
        {'k': '4'},
        {'k': True},
    ],
)
def test_search_rejects_bad_input_with_400(client, store, override):
    response = client.post('/v1/search', json=search_body(**override))
    assert response.status_code == 400
    assert store.searches == []


@pytest.mark.parametrize('missing', ['tenant_id', 'query'])
def test_search_needs_tenant_and_query(client, missing):
    body = search_body()
    del body[missing]
    assert client.post('/v1/search', json=body).status_code == 400


@pytest.mark.parametrize(
    'error',
    [PoolTimeout('no connection'), psycopg.errors.QueryCanceled('statement timeout')],
    ids=['postgres-down', 'too-slow'],
)
def test_search_is_503_when_the_database_cannot_answer(client, store, error):
    store.error = error
    response = client.post('/v1/search', json=search_body())
    assert response.status_code == 503
    assert response.json() == {'detail': 'database unavailable'}


# ---------------------------------------------------------------- model thread


def test_searches_jump_ahead_of_queued_ingestion_work():
    busy, release = threading.Event(), threading.Event()

    class SlowFirstCall(FakeEmbedder):
        def embed(self, texts):
            if texts == ['bulk-0']:
                busy.set()
                release.wait(5)
            return super().embed(texts)

    embedder = SlowFirstCall()
    model = Model(lambda: embedder)
    model.start()
    wait_until(lambda: model.name == MODEL)

    first = model.submit(['bulk-0'], BULK)
    assert busy.wait(5)  # the model thread is busy with it
    queued = [model.submit([f'bulk-{i}'], BULK) for i in (1, 2)]
    question = model.submit(['question'], SEARCH)
    release.set()
    for future in (first, *queued, question):
        future.result(5)
    model.close()

    assert embedder.calls == [['bulk-0'], ['question'], ['bulk-1'], ['bulk-2']]


def test_a_model_error_goes_to_the_request_and_the_thread_keeps_serving():
    class Broken(FakeEmbedder):
        def embed(self, texts):
            if texts == ['bad']:
                raise RuntimeError('boom')
            return super().embed(texts)

    model = Model(Broken)
    model.start()
    wait_until(lambda: model.name == MODEL)

    with pytest.raises(RuntimeError, match='boom'):
        model.submit(['bad'], SEARCH).result(5)
    assert model.submit(['good'], SEARCH).result(5).shape == (1, 1024)
    model.close()
