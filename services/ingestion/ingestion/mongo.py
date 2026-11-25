"""Access to the documents service's MongoDB (collection documents_document).

The documents service (Django) creates one record per upload with
ingestion_status = "pending". This worker moves it through:

    pending ──claim_next──> processing ──mark_completed──> completed
                                │
                                └──────mark_failed──────> failed (+ error_message)

Field formats, as written by django-mongodb-backend:
  _id               Mongo ObjectId (we use it to address the record: always unique)
  id, tenant_id     UUID as 32 hex chars, no dashes ("cfc05ea13de64b6ca96347343cb10651")
  file              path relative to MEDIA_ROOT: tenants/<tenant uuid>/<doc uuid>/<name>
  ingestion_status  pending | processing | completed | failed
  updated_at        last change; we also use it to detect stuck documents
"""

from datetime import UTC, datetime, timedelta

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection

COLLECTION = 'documents_document'

PENDING = 'pending'
PROCESSING = 'processing'
COMPLETED = 'completed'
FAILED = 'failed'

# The portal shows error_message to the tenant (spec FR-03): keep it short.
MAX_ERROR_LENGTH = 300


def _now() -> datetime:
    return datetime.now(UTC)


def connect(uri: str, db_name: str) -> Collection:
    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    client.admin.command('ping')  # fail fast at startup, not on the first document
    return client[db_name][COLLECTION]


def count_pending(documents: Collection) -> int:
    return documents.count_documents({'ingestion_status': PENDING})


def claim_next(documents: Collection) -> dict | None:
    """Take the oldest pending document and mark it "processing", atomically.

    find_one_and_update finds AND changes the record in a single operation inside
    MongoDB. If two workers ask at the same moment, MongoDB gives the document to
    only one of them; the other gets the next one (or None). Without this, both
    workers could process Company B's PDF and write its chunks twice.

    Returns the document as it is AFTER the change, or None if nothing is pending.
    """
    return documents.find_one_and_update(
        {'ingestion_status': PENDING},
        {'$set': {'ingestion_status': PROCESSING, 'error_message': '', 'updated_at': _now()}},
        sort=[('created_at', ASCENDING)],  # oldest upload first: fair for every tenant
        return_document=ReturnDocument.AFTER,
    )


def reset_stuck(documents: Collection, minutes: int = 10) -> int:
    """Put documents stuck in "processing" for more than `minutes` back to "pending".

    A document stays "processing" forever if the worker dies in the middle (Docker
    stopped, out of memory...). This gives it back so it is processed again.
    Called at startup and from time to time in the loop.

    10 minutes is far above the normal time for one document (seconds), so a
    document that is still being worked on is never taken away.

    Returns how many documents were reset.
    """
    cutoff = _now() - timedelta(minutes=minutes)
    result = documents.update_many(
        {'ingestion_status': PROCESSING, 'updated_at': {'$lt': cutoff}},
        {'$set': {'ingestion_status': PENDING, 'updated_at': _now()}},
    )
    return result.modified_count


def release(documents: Collection, doc: dict) -> None:
    """Give a document back ("pending") because of OUR problem, not the document's.

    Example: Postgres restarted while Company B's PDF was being saved. The PDF is
    fine, so it must not be marked "failed": it goes back to the queue right away
    (instead of waiting STUCK_AFTER_MINUTES for reset_stuck) and is retried.
    """
    documents.update_one(
        {'_id': doc['_id'], 'ingestion_status': PROCESSING},
        {'$set': {'ingestion_status': PENDING, 'updated_at': _now()}},
    )


def mark_completed(documents: Collection, doc: dict) -> None:
    """Success: the document's chunks are in document_chunks."""
    documents.update_one(
        # Only if it is still ours ("processing"): if reset_stuck gave it to another
        # worker in the meantime, we don't overwrite that worker's result.
        {'_id': doc['_id'], 'ingestion_status': PROCESSING},
        {'$set': {'ingestion_status': COMPLETED, 'error_message': '', 'updated_at': _now()}},
    )


def mark_failed(documents: Collection, doc: dict, message: str) -> None:
    """Failure: store a short, tenant-readable reason (shown in the portal)."""
    documents.update_one(
        {'_id': doc['_id'], 'ingestion_status': PROCESSING},
        {
            '$set': {
                'ingestion_status': FAILED,
                'error_message': message[:MAX_ERROR_LENGTH],
                'updated_at': _now(),
            }
        },
    )
