"""Knowledge service: holds the ONE bge-m3 model in RAM, embeds and searches.

    GET  /livez       the process answers HTTP              (liveness probe)
    GET  /healthz     model loaded AND Postgres answers     (readiness probe)
    POST /v1/embed    texts -> vectors, for the ingestion worker (EMBEDDING_URL)
    POST /v1/search   a caller's question -> the tenant's best chunks, for the
                      orchestrator (hybrid search, see search.py)

One copy of the model (~3 GB of RAM) serves both. It loads in the background
(minutes on a first start, which downloads it), so /livez answers at once and
/healthz turns ok when the model is ready.

Run: python -m ingestion.api   (port from PORT, default 8100)
"""

import asyncio
import itertools
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Protocol

import numpy as np
import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from .config import KnowledgeSettings
from .embedding import DIMENSIONS, Embedder, TextEmbedder
from .search import ChunkStore, Hit

log = logging.getLogger('ingestion')

# Request limits. A chunk is ~800 characters and the model reads 512 tokens at
# most, so 8000 characters per text is plenty.
MAX_TEXTS = 64
MAX_TEXT_CHARS = 8000
MAX_QUERY_CHARS = 2000
MAX_K = 10

# Model calls run one at a time, lowest number first.
SEARCH = 0  # a caller is waiting on the line
BULK = 1  # the ingestion worker can wait a little
_STOP = 2  # after everything already queued
# Bulk texts are encoded one at a time, so a search that arrives meanwhile waits
# for one chunk (~0.7 s on 2 CPU threads), not a whole request (64 chunks: ~45 s).
# Measured: ~15% slower ingestion than slices of 4, but ~3x shorter waits on a call.
BULK_SLICE = 1

# The model failed to load (download cut, disk full...): try again after 10 s,
# then 20 s, 40 s... up to 5 min. /healthz shows the error meanwhile.
LOAD_RETRY_FIRST = 10.0
LOAD_RETRY_MAX = 300.0

PROBES = ('/livez', '/healthz')


class Store(Protocol):
    """What the API needs from the database: search.ChunkStore, or a fake in tests."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def ping(self) -> None: ...

    def search(
        self, tenant_id: uuid.UUID, model: str, vector: np.ndarray, query: str, k: int
    ) -> list[tuple[Hit, float]]: ...


class Model:
    """The ONE model, owned by ONE thread: it loads it, then runs every call.

    In the background, because loading takes seconds to minutes and /livez must
    answer meanwhile. One call at a time: each holds its own activations in RAM,
    and torch already uses all the threads it is given (EMBEDDING_THREADS), so
    parallel calls would only add memory. Searches first: a caller's question
    jumps ahead of queued ingestion work.
    """

    def __init__(self, load: Callable[[], TextEmbedder]) -> None:
        self._load = load
        self._jobs: queue.PriorityQueue = queue.PriorityQueue()
        self._order = itertools.count()  # first come, first served within a priority
        self._closed = threading.Event()
        self.name = ''  # set once the model is ready
        self.load_error = ''  # last failure, shown by /healthz until a retry works

    def start(self) -> None:
        threading.Thread(target=self._run, name='model', daemon=True).start()

    def close(self) -> None:
        """Stop the thread once the work already queued is done."""
        self._closed.set()
        self._jobs.put((_STOP, next(self._order), None, None))

    def submit(self, texts: list[str], priority: int) -> Future:
        future: Future = Future()
        self._jobs.put((priority, next(self._order), texts, future))
        return future

    async def embed(self, texts: list[str], priority: int) -> np.ndarray:
        if not self.name:
            raise HTTPException(503, 'embedding model not loaded yet')
        size = BULK_SLICE if priority == BULK else len(texts)
        slices = [
            asyncio.wrap_future(self.submit(texts[i : i + size], priority))
            for i in range(0, len(texts), size)
        ]
        return np.vstack(await asyncio.gather(*slices))

    def _run(self) -> None:
        embedder = self._load_until_ready()
        if embedder is None:
            return  # closed while loading
        self.name = embedder.model_name
        log.info('knowledge service ready (%s)', self.name)
        while True:
            _, _, texts, future = self._jobs.get()
            if future is None:
                return
            if not future.set_running_or_notify_cancel():
                continue  # its request was cancelled while waiting
            try:
                future.set_result(embedder.embed(texts))
            except Exception as exc:  # given to the waiting request
                future.set_exception(exc)

    def _load_until_ready(self) -> TextEmbedder | None:
        delay = LOAD_RETRY_FIRST
        while not self._closed.is_set():
            try:
                return self._load()
            except Exception as exc:
                self.load_error = type(exc).__name__
                log.exception('embedding model failed to load, next try in %.0f s', delay)
                self._closed.wait(delay)
                delay = min(delay * 2, LOAD_RETRY_MAX)
        return None


# Texts are embedded as given (like the worker's chunks); a question is trimmed.
Text = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT_CHARS)]
Query = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_CHARS)
]


class EmbedRequest(BaseModel):
    texts: list[Text] = Field(min_length=1, max_length=MAX_TEXTS)


class SearchRequest(BaseModel):
    tenant_id: uuid.UUID
    query: Query
    # strict: true or "4" is a client bug, not a number of chunks.
    k: int = Field(default=4, ge=1, le=MAX_K, strict=True)


def _unavailable(status: str, detail: str) -> JSONResponse:
    return JSONResponse({'status': status, 'detail': detail}, status_code=503)


def create_app(
    settings: KnowledgeSettings,
    *,
    load_embedder: Callable[[], TextEmbedder] | None = None,
    store: Store | None = None,
) -> FastAPI:
    """Build the app. Tests pass a fake model loader and store: no torch, no Postgres."""
    model = Model(
        load_embedder
        or partial(Embedder, settings.embedding_model, threads=settings.embedding_threads)
    )
    chunks: Store = store or ChunkStore(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Both start in the background: Postgres may come up after us, and the
        # model takes seconds to minutes. /livez answers meanwhile.
        chunks.open()
        model.start()
        yield
        model.close()
        chunks.close()

    app = FastAPI(title='knowledge', version='1.0.0', lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 400, not FastAPI's 422 (API contract). Says where and what is wrong, but
        # does not echo the input back: it can be a whole document.
        errors = [{'loc': list(error['loc']), 'msg': error['msg']} for error in exc.errors()]
        return JSONResponse({'detail': errors}, status_code=400)

    @app.get('/livez')
    async def livez() -> dict:
        return {'status': 'alive'}

    @app.get('/healthz')
    async def healthz() -> JSONResponse:
        if not model.name:
            if model.load_error:
                detail = f'embedding model failed to load ({model.load_error}), retrying'
                return _unavailable('unavailable', detail)
            return _unavailable('starting', 'loading the embedding model')
        try:
            await run_in_threadpool(chunks.ping)
        except psycopg.Error as exc:  # down, restarting, or grants not applied yet
            return _unavailable('unavailable', f'database unavailable ({type(exc).__name__})')
        return JSONResponse({'status': 'ok', 'model': model.name, 'dimensions': DIMENSIONS})

    @app.post('/v1/embed')
    async def embed(body: EmbedRequest) -> JSONResponse:
        vectors = await model.embed(body.texts, BULK)
        # Straight to JSON: 64 x 1024 floats through FastAPI's encoder is slow for
        # nothing. float32 -> float -> JSON -> float32 gives back the same numbers.
        return JSONResponse(
            {'model': model.name, 'dimensions': int(vectors.shape[1]), 'vectors': vectors.tolist()}
        )

    @app.post('/v1/search')
    async def search(body: SearchRequest) -> dict:
        started = time.perf_counter()
        vector = (await model.embed([body.query], SEARCH))[0]
        try:
            found = await run_in_threadpool(
                chunks.search, body.tenant_id, model.name, vector, body.query, body.k
            )
        except psycopg.OperationalError as exc:
            # Postgres down, restarting or too slow: the caller gets an answer
            # without documents rather than a long silence.
            log.warning('search failed: database unavailable (%s)', type(exc).__name__)
            raise HTTPException(503, 'database unavailable') from exc
        return {
            'chunks': [
                {
                    'document_id': hit.document_id,
                    'document_title': hit.document_title,
                    'section': hit.section,
                    'content': hit.content,
                    'score': score,
                }
                for hit, score in found
            ],
            'took_ms': round((time.perf_counter() - started) * 1000),
        }

    return app


def _not_a_probe(record: logging.LogRecord) -> bool:
    # uvicorn.access arguments: (client, method, path, http version, status).
    args = record.args
    return not (isinstance(args, tuple) and len(args) > 2 and args[2] in PROBES)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    # The pool logs every checkout at INFO: only its problems are worth reading.
    logging.getLogger('psycopg.pool').setLevel(logging.WARNING)
    settings = KnowledgeSettings.from_env()
    config = uvicorn.Config(
        create_app(settings),
        # All interfaces: in a container the port is only reachable through what
        # Docker publishes or the Kubernetes Service exposes.
        host='0.0.0.0',  # noqa: S104
        port=settings.port,
        # Longer than clients keep an idle connection (httpx: 5 s), so the client
        # closes it first: never a request sent on a connection we just closed.
        timeout_keep_alive=75,
    )
    # Probes hit /livez and /healthz every few seconds: keep them out of the log.
    logging.getLogger('uvicorn.access').addFilter(_not_a_probe)
    uvicorn.Server(config).run()


if __name__ == '__main__':
    main()
