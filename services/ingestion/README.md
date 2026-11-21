# Ingestion worker

Background worker that turns uploaded documents into searchable chunks for RAG.

```
documents service (Mongo + uploads volume)  ->  ingestion worker  ->  document_chunks (Postgres + pgvector)
```

It has no HTTP API. It reads documents with `ingestion_status = "pending"` from the
documents service's MongoDB, reads their files from the shared uploads volume
(read-only), and writes chunks + embeddings to `document_chunks` in the tenant-portal
Postgres. The table is created by `services/tenant-portal/db/postgres/02-pgvector.sql`.

## Status

Skeleton: it connects to both databases, checks that pgvector and `document_chunks`
exist, and logs how many documents are pending. Still to do:

| Step | What |
|---|---|
| 4 | Claim a pending document (`pending` -> `processing`) |
| 5 | Extract text (pdf, docx, xlsx, csv, txt) |
| 6 | Split into chunks |
| 7 | Embed with `BAAI/bge-m3` (1024 dims, normalized) |
| 8 | Replace the document's rows in `document_chunks` |
| 9 | Set `completed`, or `failed` with `error_message` |

## Data formats to know

- Mongo stores `id` and `tenant_id` as 32 hex chars without dashes. Convert with
  `uuid.UUID(value)` before writing them to the `UUID` columns in Postgres.
- `file` is a path relative to `MEDIA_ROOT`: `tenants/<tenant uuid>/<doc uuid>/<name>`.

## Run locally (Docker)

1. Start the two stacks it depends on:
   - `services/documents`: `docker compose up -d`
   - `services/tenant-portal`: `docker compose up -d db`
2. Here: copy `.env.example` to `.env` and use the same passwords as those two stacks.
3. `docker compose up --build`

Expected log:

```
connected to MongoDB (documents.documents_document)
connected to Postgres (pgvector and document_chunks present)
3 document(s) pending (processing not implemented yet)
```

## Tests and lint

```bash
pip install -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

## Known gaps

- Uses the documents app's Mongo user and the portal's Postgres user. A dedicated user
  for each (Mongo: readWrite on `documents_document` only; Postgres: rights on
  `document_chunks` only) is cleaner.
- Uploads live on a local Docker volume (documents service NFR-01 gap), so this worker
  must run on the same Docker host until files move to object storage.
