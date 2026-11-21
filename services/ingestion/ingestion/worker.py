"""Ingestion worker: turns uploaded documents into rows of document_chunks.

For each pending document (one at a time):

    1. claim         mongo.claim_next          pending -> processing
    2. read file     storage.read_document_file
    3. extract text  chunking.extract_text
    4. clean         cleaning.clean_text
    5. chunk         chunking.chunk_document + cleaning.drop_tiny
    6. embed         embedding.Embedder.embed
    7. store         postgres.replace_chunks   one transaction
    8. status        mongo.mark_completed / mark_failed (or release)

All the heavy work happens here, offline and once per document, so live calls
only have to run a quick search (RAG phase 1).

Run: python -m ingestion.worker
"""

import logging
import signal
import threading
import time
from pathlib import Path

import psycopg
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from . import mongo, postgres
from .chunking import chunk_document, extract_text
from .cleaning import clean_text, drop_tiny
from .config import Settings
from .embedding import Embedder
from .errors import IngestionError
from .storage import read_document_file

log = logging.getLogger('ingestion')

# How often (seconds) the loop gives back documents stuck in "processing".
RESET_STUCK_EVERY = 60


def _extract(filename: str, data: bytes) -> str:
    """extract_text(), with errors turned into messages a tenant understands."""
    try:
        return extract_text(filename, data)
    except ValueError as exc:  # "Unsupported file type: .pptx"
        raise IngestionError(str(exc)) from exc
    except Exception as exc:  # pypdf / python-docx / openpyxl could not parse it
        raise IngestionError('Could not read the file (damaged or password-protected?).') from exc


def process_one(
    doc: dict,
    documents: Collection,
    conn: psycopg.Connection,
    embedder: Embedder,
    settings: Settings,
) -> None:
    """Run the whole pipeline for ONE claimed document and set its final status.

    Postgres connection errors are NOT caught here: they are our problem, not the
    document's, so the caller gives the document back and reconnects.
    """
    started = time.perf_counter()
    # Only ids in the logs: never the document's text (tenant data).
    doc_ref = f'document {doc.get("id")} (tenant {doc.get("tenant_id")})'
    try:
        filename, data = read_document_file(doc, settings.media_root)
        text = clean_text(_extract(filename, data))
        chunks = drop_tiny(chunk_document(text, fallback_title=Path(filename).stem))
        if not chunks:
            raise IngestionError('No usable text found in the document.')
        vectors = embedder.embed([chunk.content for chunk in chunks])
        stored = postgres.replace_chunks(conn, doc, chunks, vectors, embedder.model_name)
    except IngestionError as exc:
        # The document's problem: tell the tenant why (shown in the portal).
        log.warning('%s failed: %s', doc_ref, exc)
        mongo.mark_failed(documents, doc, str(exc))
        return
    except psycopg.OperationalError:
        raise  # connection problem: handled by the main loop
    except Exception as exc:
        # A bug on our side: full details in our log, a generic message for the tenant.
        log.exception('%s failed with an unexpected error', doc_ref)
        mongo.mark_failed(documents, doc, f'Processing failed ({type(exc).__name__}).')
        return

    mongo.mark_completed(documents, doc)
    log.info('%s completed: %d chunks in %.1f s', doc_ref, stored, time.perf_counter() - started)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    settings = Settings.from_env()

    documents = mongo.connect(settings.mongodb_uri, settings.mongodb_db)
    log.info('connected to MongoDB (%s.%s)', settings.mongodb_db, mongo.COLLECTION)
    conn = postgres.connect(settings.database_url)
    log.info('connected to Postgres (pgvector, document_chunks and RAG columns present)')
    if not settings.media_root.is_dir():
        log.warning('MEDIA_ROOT %s is not a directory: uploads cannot be read', settings.media_root)

    # Slow (model load), so done once, before taking any document.
    embedder = Embedder(settings.embedding_model, threads=settings.embedding_threads)

    # Stop cleanly on SIGTERM (docker stop) or Ctrl+C. The document being processed
    # is finished first; the loop stops before taking the next one.
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    last_reset = 0.0
    try:
        while not stop.is_set():
            try:
                # From time to time: give back documents of a worker that died.
                if time.monotonic() - last_reset >= RESET_STUCK_EVERY:
                    reset = mongo.reset_stuck(documents, settings.stuck_after_minutes)
                    if reset:
                        log.warning('%d stuck document(s) put back to pending', reset)
                    last_reset = time.monotonic()

                doc = mongo.claim_next(documents)
                if doc is None:
                    stop.wait(settings.poll_interval)  # nothing to do: wait, then look again
                    continue

                if conn.broken or conn.closed:
                    conn = postgres.connect(settings.database_url)
                    log.info('reconnected to Postgres')
                try:
                    process_one(doc, documents, conn, embedder, settings)
                except psycopg.OperationalError:
                    # Postgres went away mid-document. Not the document's fault:
                    # give it back now, reconnect on the next round.
                    log.exception('Postgres connection lost; document %s put back', doc.get('id'))
                    mongo.release(documents, doc)
                    stop.wait(settings.poll_interval)

            except (PyMongoError, psycopg.OperationalError):
                # Mongo or Postgres unreachable (restart...): wait and try again,
                # instead of crashing the worker.
                log.exception('database unavailable, retrying in %s s', settings.poll_interval)
                stop.wait(settings.poll_interval)
    finally:
        conn.close()
        log.info('stopped')


if __name__ == '__main__':
    main()
