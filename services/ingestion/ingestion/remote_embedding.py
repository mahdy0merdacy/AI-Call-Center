"""Embed through the knowledge service instead of loading bge-m3 in this process.

In the core app, the knowledge service (python -m ingestion.api) holds the ONE copy
of the model (~3 GB of RAM) and serves both live searches and this worker. Set
EMBEDDING_URL (e.g. http://knowledge:8100) to use it; without it the worker loads
the model itself (embedding.Embedder), as before.

Same interface as Embedder: .model_name, and .embed(texts) -> float32 (n, 1024).
"""

import logging
import threading
import time
from collections.abc import Callable

import httpx
import numpy as np

from .embedding import DIMENSIONS, MODEL_NAME

log = logging.getLogger('ingestion')

# Texts per request (the API takes up to 64). Smaller requests redo less work after
# a failure.
BATCH = 32
# The API refuses longer texts. The model reads only the first 512 tokens, a few
# thousand characters at most, so cutting here gives the same vector.
MAX_TEXT_CHARS = 8000
# Answers worth retrying: the service is starting, restarting or overloaded. Any
# other error (400, 500...) would come back the same on a retry.
RETRY_STATUSES = frozenset({429, 502, 503, 504})
# Wait between tries: 1 s, 2 s, 4 s... up to MAX_DELAY.
FIRST_DELAY = 1.0
MAX_DELAY = 30.0
# One embed call gives up after this long. The worker then puts the document back
# to pending (not the document's fault) and tries again later.
RETRY_FOR = 120.0
# Once connected, be patient: a batch waits behind live searches, which go first.
TIMEOUT = httpx.Timeout(300.0, connect=5.0)
PROBE_TIMEOUT = 5.0


class EmbeddingUnavailable(Exception):
    """The knowledge service did not answer in time. Not the document's fault."""


def _reason(response: httpx.Response) -> str:
    """'HTTP 503 starting': the status code, plus the service's own status if any."""
    try:
        body = response.json()
    except ValueError:
        body = None
    status = body.get('status', '') if isinstance(body, dict) else ''
    return f'HTTP {response.status_code} {status}'.strip()


class RemoteEmbedder:
    """Calls POST /v1/embed of the knowledge service. Waits for it at startup."""

    def __init__(
        self,
        url: str,
        model_name: str = MODEL_NAME,
        *,
        stop: threading.Event | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # stop: set on SIGTERM, ends waits early. client and clock: for tests.
        self.model_name = model_name
        self._url = url.rstrip('/')
        self._stop = stop or threading.Event()
        # trust_env=False: a call inside the cluster never goes through an HTTP_PROXY
        # that the environment may set for outside traffic.
        self._client = client or httpx.Client(base_url=self._url, timeout=TIMEOUT, trust_env=False)
        self._clock = clock

        self._wait_until_ready()
        # Same safety check as Embedder: one real embedding, end to end. _vectors()
        # stops the worker here if the vectors do not fit vector(1024).
        dims = self.embed(['warm-up']).shape[1]
        log.info('embedding through %s (%s, %d dimensions)', self._url, model_name, dims)

    def _wait_until_ready(self) -> None:
        """Block until /healthz is ok: model loaded and Postgres answering."""
        delay = FIRST_DELAY
        while True:
            try:
                response = self._client.get('/healthz', timeout=PROBE_TIMEOUT)
            except httpx.TransportError as exc:  # not started yet, name not resolvable...
                reason = type(exc).__name__
            else:
                if response.status_code == 200:
                    self._check_model(response.json().get('model'))
                    return
                reason = _reason(response)
            log.info('waiting for the knowledge service (%s), next try in %.0f s', reason, delay)
            if self._stop.wait(delay):
                raise EmbeddingUnavailable('stopping')
            delay = min(delay * 2, MAX_DELAY)

    def _check_model(self, served: object) -> None:
        # Every row stores embedding_model, and searches compare only vectors of the
        # same model: rows labelled with the wrong name would never be found.
        if served != self.model_name:
            raise RuntimeError(
                f'the knowledge service embeds with {served!r} but EMBEDDING_MODEL is '
                f'{self.model_name!r}: set the same model on both'
            )

    def embed(self, texts: list[str]) -> np.ndarray:
        """Shape (len(texts), 1024), float32, normalized: what Embedder.embed returns."""
        parts = [self._embed_batch(texts[i : i + BATCH]) for i in range(0, len(texts), BATCH)]
        return np.vstack(parts) if parts else np.empty((0, DIMENSIONS), dtype=np.float32)

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        payload = {'texts': [text[:MAX_TEXT_CHARS] for text in texts]}
        give_up_at = self._clock() + RETRY_FOR
        delay = FIRST_DELAY
        while True:
            try:
                response = self._client.post('/v1/embed', json=payload)
            except httpx.TransportError as exc:  # refused, reset, timed out...
                reason = type(exc).__name__
            else:
                if response.status_code == 200:
                    return self._vectors(response.json(), len(texts))
                if response.status_code not in RETRY_STATUSES:
                    raise RuntimeError(f'knowledge service answered {_reason(response)}')
                reason = _reason(response)
            if self._clock() + delay > give_up_at:
                raise EmbeddingUnavailable(f'knowledge service unavailable ({reason})')
            log.warning('embedding failed (%s), retrying in %.0f s', reason, delay)
            if self._stop.wait(delay):
                raise EmbeddingUnavailable('stopping')
            delay = min(delay * 2, MAX_DELAY)

    def _vectors(self, body: dict, count: int) -> np.ndarray:
        self._check_model(body.get('model'))
        # JSON numbers are the float32 values written as floats: converting back
        # gives exactly the vectors the model produced.
        vectors = np.asarray(body.get('vectors'), dtype=np.float32)
        if vectors.shape != (count, DIMENSIONS):
            raise RuntimeError(
                f'knowledge service gave vectors of shape {vectors.shape}, expected '
                f'({count}, {DIMENSIONS}): document_chunks.embedding is vector({DIMENSIONS})'
            )
        return vectors
