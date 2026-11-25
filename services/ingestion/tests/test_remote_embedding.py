"""RemoteEmbedder against a fake knowledge service (httpx.MockTransport).

No real waiting: a fake stop event records each backoff delay and moves a fake
clock instead of sleeping.
"""

import json
import zlib

import httpx
import numpy as np
import pytest

from ingestion.remote_embedding import (
    BATCH,
    MAX_TEXT_CHARS,
    RETRY_FOR,
    EmbeddingUnavailable,
    RemoteEmbedder,
)

URL = 'http://knowledge:8100'
MODEL = 'BAAI/bge-m3'


def vector_for(text: str) -> np.ndarray:
    rng = np.random.default_rng(zlib.crc32(text.encode()))
    vector = rng.standard_normal(1024).astype(np.float32)
    return vector / np.linalg.norm(vector)


class FakeService:
    """The knowledge API. Scripted answers first (an exception, a status code or a
    Response), then it behaves: /healthz ok, /v1/embed one vector per text."""

    def __init__(self, health=(), embed=(), model=MODEL):
        self.health = list(health)
        self.embed = list(embed)
        self.model = model
        self.batches = []  # texts of every /v1/embed request

    def __call__(self, request: httpx.Request) -> httpx.Response:
        is_health = request.url.path == '/healthz'
        if not is_health:
            self.batches.append(json.loads(request.content)['texts'])
        script = self.health if is_health else self.embed
        if script:
            answer = script.pop(0)
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, int):
                return httpx.Response(answer, json={'status': 'starting', 'detail': '...'})
            return answer
        body = {'model': self.model, 'dimensions': 1024}
        if is_health:
            return httpx.Response(200, json={'status': 'ok', **body})
        vectors = [vector_for(t).tolist() for t in self.batches[-1]]
        return httpx.Response(200, json={**body, 'vectors': vectors})


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeStop:
    """Stands in for the worker's stop Event: never sleeps, advances the clock."""

    def __init__(self, clock, stopped=False):
        self.clock = clock
        self.stopped = stopped
        self.waits = []

    def wait(self, timeout):
        self.waits.append(timeout)
        self.clock.now += timeout
        return self.stopped


def connect(service, stopped=False):
    clock = Clock()
    stop = FakeStop(clock, stopped)
    client = httpx.Client(base_url=URL, transport=httpx.MockTransport(service))
    embedder = RemoteEmbedder(URL, MODEL, stop=stop, client=client, clock=clock)
    return embedder, stop


# ---------------------------------------------------------------- startup


def test_waits_for_the_service_with_capped_exponential_backoff():
    refused = httpx.ConnectError('connection refused')
    service = FakeService(health=[refused, refused, *[503] * 7])

    embedder, stop = connect(service)

    assert stop.waits == [1, 2, 4, 8, 16, 30, 30, 30, 30]
    assert embedder.model_name == MODEL
    assert service.batches == [['warm-up']]  # startup check, end to end


def test_refuses_a_service_that_embeds_with_another_model():
    with pytest.raises(RuntimeError, match='EMBEDDING_MODEL'):
        connect(FakeService(model='intfloat/multilingual-e5-large'))


def test_refuses_vectors_that_do_not_fit_the_column():
    wrong = httpx.Response(200, json={'model': MODEL, 'dimensions': 768, 'vectors': [[0.0] * 768]})
    with pytest.raises(RuntimeError, match=r'vector\(1024\)'):
        connect(FakeService(embed=[wrong]))


# ---------------------------------------------------------------- embed


def test_embeds_in_batches_and_keeps_the_order():
    service = FakeService()
    embedder, _ = connect(service)
    texts = [f'chunk {i}' for i in range(70)]

    vectors = embedder.embed(texts)

    assert [len(batch) for batch in service.batches[1:]] == [BATCH, BATCH, 70 - 2 * BATCH]
    assert vectors.dtype == np.float32
    assert vectors.shape == (70, 1024)
    assert np.array_equal(vectors, np.stack([vector_for(t) for t in texts]))


def test_cuts_texts_to_what_the_api_accepts():
    service = FakeService()
    embedder, _ = connect(service)
    embedder.embed(['x' * (MAX_TEXT_CHARS + 500)])
    assert len(service.batches[-1][0]) == MAX_TEXT_CHARS


def test_retries_transient_errors_with_backoff():
    service = FakeService()
    embedder, stop = connect(service)
    service.embed = [httpx.ReadTimeout('slow'), 503, httpx.RemoteProtocolError('closed'), 502]

    vectors = embedder.embed(['Records are kept for 10 years.'])

    assert stop.waits == [1, 2, 4, 8]
    assert np.array_equal(vectors[0], vector_for('Records are kept for 10 years.'))


def test_gives_up_after_retry_for_so_the_document_goes_back_to_pending():
    service = FakeService()
    embedder, stop = connect(service)
    service.embed = [503] * 50

    with pytest.raises(EmbeddingUnavailable, match='HTTP 503 starting'):
        embedder.embed(['text'])

    assert stop.waits == [1, 2, 4, 8, 16, 30, 30]
    assert sum(stop.waits) <= RETRY_FOR


@pytest.mark.parametrize('status', [400, 500])
def test_does_not_retry_what_would_fail_again(status):
    service = FakeService()
    embedder, stop = connect(service)
    service.embed = [status]

    with pytest.raises(RuntimeError, match=f'HTTP {status}'):
        embedder.embed(['text'])
    assert stop.waits == []


def test_stopping_ends_the_retries_at_once():
    service = FakeService()
    embedder, stop = connect(service)
    stop.stopped = True  # SIGTERM arrived
    service.embed = [503] * 5

    with pytest.raises(EmbeddingUnavailable, match='stopping'):
        embedder.embed(['text'])
    assert stop.waits == [1]


def test_empty_input_needs_no_request():
    service = FakeService()
    embedder, _ = connect(service)
    assert embedder.embed([]).shape == (0, 1024)
    assert service.batches == [['warm-up']]
