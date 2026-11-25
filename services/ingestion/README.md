# Ingestion worker and knowledge API

Background worker that turns uploaded documents into searchable chunks for RAG, and
the knowledge API that searches them during calls. Same image, two programs.

```
documents service (Mongo + uploads volume)  ->  ingestion worker  ->  document_chunks (Postgres + pgvector)
                                                       | embeds through         ^ searches (read-only)
                                                       v                        |
                                               knowledge API (bge-m3, port 8100)  <-  orchestrator
```

The worker has no HTTP API. It takes documents with `ingestion_status = "pending"` from
the documents service's MongoDB, reads their files from the shared uploads volume
(read-only), and writes chunks + embeddings to `document_chunks` in the tenant-portal
Postgres (created by `services/tenant-portal/db/postgres/02-pgvector.sql` and
`03-rag-columns.sql`).

All the heavy work (extraction, embedding) happens offline and once per document,
so live calls only run a quick search (see [Knowledge API](#knowledge-api)).

## Pipeline (one document at a time)

| # | Step | Code | Notes |
|---|---|---|---|
| 1 | Claim | `mongo.claim_next` | `pending` -> `processing`, atomic: two workers never take the same document |
| 2 | Read | `storage.read_document_file` | only inside `MEDIA_ROOT`; the one place to change for S3 |
| 3 | Extract | `chunking.extract_text` | pdf (pages joined with `\f`), docx, xlsx, csv, txt |
| 4 | Clean | `cleaning.clean_text` | NFKC, spaces, repeated headers/footers, page numbers |
| 5 | Chunk | `chunking.chunk_document` + `cleaning.drop_tiny` | `[Title > Section]` prefix, codes like `SCP-101` |
| 6 | Embed | `embedding.Embedder` | `BAAI/bge-m3`, 1024 dims, normalized, loaded once; with `EMBEDDING_URL`, `remote_embedding.RemoteEmbedder` asks the knowledge API instead |
| 7 | Store | `postgres.replace_chunks` | ONE transaction: delete the document's rows, insert new ones; duplicates skipped |
| 8 | Status | `mongo.mark_completed` / `mark_failed` / `release` | see below |

### What happens when something goes wrong

| Problem | Status | `error_message` (shown in the portal) |
|---|---|---|
| No text (scanned PDF) | `failed` | No text found in the document (scanned PDF?). |
| Damaged / password-protected file | `failed` | Could not read the file (damaged or password-protected?). |
| Unsupported type | `failed` | Unsupported file type: .pptx |
| File missing from storage | `failed` | File not found in storage. Please upload it again. |
| Bug in our code | `failed` | Processing failed (ErrorType). Details only in the worker log. |
| Postgres connection lost | back to `pending` | none: not the document's fault, retried after reconnecting |
| Knowledge API down for 2 min (`EMBEDDING_URL`) | back to `pending` | none: retried with backoff first, then later |
| Worker killed mid-document | back to `pending` after `STUCK_AFTER_MINUTES` | none |

Logs contain only ids, counts and timings, never document text.

## Data formats to know

- Mongo stores `id` and `tenant_id` as 32 hex chars without dashes. `cleaning.to_uuid`
  converts them for the `UUID` columns; a malformed id is refused, never written.
- `file` is a path relative to `MEDIA_ROOT`: `tenants/<tenant uuid>/<doc uuid>/<name>`.
- Every row stores `embedding_model`. Questions must be embedded with the same model
  and normalization, or distances mean nothing: the knowledge API does both, and only
  compares rows of the model it has loaded.

## Run locally (Docker)

1. Check Docker Desktop > Settings > Resources > Memory: at least 6 GB. The worker
   alone needs ~3 GB for bge-m3; if its log ends with `Killed`, this is why.
2. Start the two stacks it depends on:
   - `services/documents`: `docker compose up -d`
   - `services/tenant-portal`: `docker compose up -d db`
3. Here: copy `.env.example` to `.env` and use the same passwords as those two stacks.
4. `docker compose up --build`

The first start downloads the model (~2.3 GB) into the `hf_cache` volume; later
starts reuse it. Expected log:

```
connected to MongoDB (documents.documents_document)
connected to Postgres (pgvector, document_chunks and RAG columns present)
loading embedding model BAAI/bge-m3 (first start downloads ~2.3 GB)...
embedding model ready (1024 dimensions, 2 CPU threads)
document 5c33... (tenant cfc0...) completed: 14 chunks in 4.2 s
```

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | changing it means re-embedding every document |
| `EMBEDDING_THREADS` | `2` | CPU threads for embedding; kept low next to live calls |
| `EMBEDDING_URL` | unset | e.g. `http://knowledge:8100`: embed through the knowledge API (no model in the worker, ~3 GB less RAM). Unset: load the model here, as above |
| `STUCK_AFTER_MINUTES` | `10` | `processing` longer than this goes back to `pending` |
| `POLL_INTERVAL_SECONDS` | `5` | wait between two looks when nothing is pending |
| `WORKER_CPUS` / `WORKER_MEMORY` | `2` / `4g` | Docker limits (compose), so ingestion can't starve live calls |

## Knowledge API

`python -m ingestion.api` (same image, other command): holds the ONE copy of bge-m3 in
RAM, embeds for the worker and searches for the orchestrator. Listens on `0.0.0.0:$PORT`.

| Endpoint | Answer |
|---|---|
| `GET /livez` | `200 {"status":"alive"}` as soon as the process serves HTTP (liveness probe) |
| `GET /healthz` | `200 {"status":"ok","model":"BAAI/bge-m3","dimensions":1024}` once the model is loaded AND Postgres answers; else `503 {"status":"starting"\|"unavailable","detail":...}` (readiness probe) |
| `POST /v1/embed` | `{"texts":[...]}` (1-64 texts, 1-8000 characters each) -> `{"model","dimensions","vectors"}`: exactly `Embedder.embed`, same order |
| `POST /v1/search` | `{"tenant_id":"<uuid>","query":"...","k":4}` (query 1-2000 characters, k 1-10) -> `{"chunks":[{"document_id","document_title","section","content","score"}],"took_ms"}` |

Invalid input is a `400`; the model not loaded yet or Postgres unreachable is a `503`.

How a search works (`search.py`): the tenant's top 20 chunks by meaning (cosine
distance, hnsw index) and its top 20 by words (`websearch_to_tsquery('simple', ...)`,
`ts_rank_cd`), fused with Reciprocal Rank Fusion (`1 / (60 + rank)`), best `k` returned.
**Both queries filter on `tenant_id` AND `embedding_model`**: a tenant never gets
another tenant's chunks. With pgvector >= 0.8, iterative index scans keep the filtered
vector query complete.

Design points:

- The model loads in the background: `/livez` answers at once, `/healthz` turns ok
  when it is ready (seconds from the cache; the first start downloads ~2.3 GB). A
  failed load is retried (10 s, doubling up to 5 min) and shown in `/healthz`.
- ONE thread runs every model call, one at a time (RAM stays at one call's worth), and
  a caller's question goes before queued ingestion work. Ingestion texts are encoded
  one by one, so a question waits for one chunk at most (~0.7 s on 2 threads), never a
  whole batch.
- Postgres is reached through a small pool (`voice_reader`, read-only) that connects
  in the background and replaces broken connections: start order does not matter, and
  a Postgres restart needs no restart here. `statement_timeout` is 2 s.
- No document text is logged; the probe requests are kept out of the access log.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | required | read-only DSN (`voice_reader`: `SELECT` on `document_chunks`) |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | must be the model the worker stored (`embedding_model` column) |
| `EMBEDDING_THREADS` | `2` | CPU threads for the model |
| `PORT` | `8100` | HTTP port |
| `HF_HOME` | `/models/huggingface` | model cache (a volume, so the download happens once) |
| `HF_HUB_OFFLINE` | unset | `1`: never contact the Hub (the model must be in `HF_HOME` already) |

Needs ~3 GB of RAM. The image has no curl; a compose healthcheck can use Python:
`python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=3)"`.

The worker with `EMBEDDING_URL` set waits (backoff 1 s doubling to 30 s) until
`/healthz` is ok and serves the same model as its `EMBEDDING_MODEL`, checks one
embedding end to end, then sends batches of 32. Connection errors, timeouts and
429/502/503/504 are retried for up to 2 minutes; after that the document goes back to
`pending` (not its fault) and is tried again later.

## Dev scripts (Mongo)

`scripts/` holds two mongosh scripts for testing. They are not part of the image.
Run them from **`services/documents`** (where the `mongo` service is), each as ONE line.
Parameters are passed with `-e NAME=value`, which avoids the PowerShell 5.1 quoting
problem with `--eval "..."`.

| Script | What | Changes data? |
|---|---|---|
| `list_documents.js` | every document of every tenant: id, tenant, status, file, error | no |
| `set_status.js` | set chosen documents to `pending` or `failed` | only with `-e CONFIRM=yes` (otherwise a dry run) |

```powershell
# List (optionally: -e STATUS=pending)
Get-Content ../ingestion/scripts/list_documents.js | docker compose exec -T mongo sh -c 'cat > /tmp/s.js && mongosh --quiet -u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin documents /tmp/s.js'

# Put documents back to pending, to process them again (dry run; add -e CONFIRM=yes to apply)
Get-Content ../ingestion/scripts/set_status.js | docker compose exec -T -e TO=pending -e FILES=stock_document.pdf mongo sh -c 'cat > /tmp/s.js && mongosh --quiet -u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin documents /tmp/s.js'
```

The real product needs tenant-scoped **Retry** and **Delete** endpoints in the documents
service (planned with document versions, step 3b); these scripts are only for us.

## Tests and lint

No database, model or Docker needed:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check . ; .venv\Scripts\ruff format --check .
```

## Known gaps

- Uses the documents app's Mongo user and the portal's Postgres user. A dedicated user
  for each (Mongo: readWrite on `documents_document` only; Postgres: rights on
  `document_chunks` only) is cleaner.
- Uploads live on a local Docker volume (documents service NFR-01 gap), so this worker
  must run on the same Docker host until files move to object storage.
- A file that crashes the worker every time (e.g. out of memory) loops
  `processing` -> `pending` forever. Add a retry counter if it ever happens.
- `chunking.CODE` also matches document ids (`Document ID: STK-001` -> codes `STK-001`).
- Chunks of different rules can share a section name ("Stock Control Code" for both
  SCP-101 and SCP-102): document versions (step 3b) must include the code in the key.
- The words query needs EVERY word of the question in a chunk (`websearch_to_tsquery`
  joins them with AND, and `simple` keeps "how", "the"...). It catches codes and short
  questions; long spoken questions are found by the meaning query.
- One knowledge API = one model thread: a question embeds in ~0.2 s on 2 threads, so
  many simultaneous calls queue. Scale with replicas (~3 GB each) or more threads.
