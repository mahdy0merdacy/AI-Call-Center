"""Access to the documents service's MongoDB (collection documents_document).

Field formats, as written by django-mongodb-backend:
  id, tenant_id     UUID as 32 hex chars, no dashes ("cfc05ea13de64b6ca96347343cb10651")
  file              path relative to MEDIA_ROOT: tenants/<tenant uuid>/<doc uuid>/<name>
  ingestion_status  pending | processing | completed | failed
"""

from pymongo import MongoClient
from pymongo.collection import Collection

COLLECTION = 'documents_document'

PENDING = 'pending'
PROCESSING = 'processing'
COMPLETED = 'completed'
FAILED = 'failed'


def connect(uri: str, db_name: str) -> Collection:
    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    client.admin.command('ping')  # fail fast at startup, not on the first document
    return client[db_name][COLLECTION]


def count_pending(documents: Collection) -> int:
    return documents.count_documents({'ingestion_status': PENDING})
