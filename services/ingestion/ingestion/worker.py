"""Ingestion worker: turns uploaded documents into rows of document_chunks.

Skeleton for now: it connects to both databases, then reports how many documents
are waiting. The pipeline is filled in step by step:

  4. claim a pending document (pending -> processing)
  5. extract its text (pdf, docx, xlsx, csv, txt)
  6. split it into chunks
  7. embed each chunk with BAAI/bge-m3 (1024 dims)
  8. replace the document's rows in document_chunks
  9. set ingestion_status to completed, or failed with error_message

Run: python -m ingestion.worker
"""

import logging
import signal
import threading

from . import mongo, postgres
from .config import Settings

log = logging.getLogger('ingestion')


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    settings = Settings.from_env()

    documents = mongo.connect(settings.mongodb_uri, settings.mongodb_db)
    log.info('connected to MongoDB (%s.%s)', settings.mongodb_db, mongo.COLLECTION)
    conn = postgres.connect(settings.database_url)
    log.info('connected to Postgres (pgvector and document_chunks present)')
    if not settings.media_root.is_dir():
        log.warning('MEDIA_ROOT %s is not a directory: uploads cannot be read', settings.media_root)

    # Stop cleanly on SIGTERM (docker stop) or Ctrl+C, between two polls.
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    try:
        while not stop.is_set():
            pending = mongo.count_pending(documents)
            if pending:
                log.info('%d document(s) pending (processing not implemented yet)', pending)
            stop.wait(settings.poll_interval)
    finally:
        conn.close()
        log.info('stopped')


if __name__ == '__main__':
    main()
