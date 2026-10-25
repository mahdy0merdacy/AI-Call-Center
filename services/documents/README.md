# Documents service

Multi-tenant document storage for the AI voice reception platform: tenants upload the
files their voice agent should know about, and create session links for their customers.
Django 5.2 + Django REST Framework, data in MongoDB.

This service **only verifies** logins. Tokens are issued by the tenant-portal (RS256);
here we hold just its public key, so this service cannot mint tokens.

## API

All endpoints need `Authorization: Bearer <access token>` except `/health/`. The tenant
always comes from the token (`tenant_id` claim), never from the request, and another
tenant's data answers 404.

| Method | Path | What |
| --- | --- | --- |
| `POST` | `/api/documents/` | Upload a file (`pdf`, `docx`, `xlsx`, `csv`, `txt`; 25 MB by default) |
| `GET` | `/api/documents/` | List the tenant's documents, newest first |
| `GET` | `/api/documents/<id>/` | One document |
| `POST` / `GET` | `/api/session-links/` | Create / list session links |
| `POST` | `/api/session-links/<id>/deactivate/` | Turn a link off |
| `GET` | `/health/` | Liveness, no token, no database query |

Status codes: `401` missing/invalid/expired token, `403` valid token with a role that may
not use this service (only `tenant_admin` by default), `404` not yours.

## Run it

```bash
cp .env.example .env     # fill in the values; JWT_PUBLIC_KEY is the portal's public key
docker compose up --build
```

Compose starts the initialized MongoDB image, a one-shot `mongo-setup` job that creates the
app's own database user (read/write on its database only, never root), and the app on
`APP_PORT` (default 8001; 8000 is the tenant-portal).

## Database

`db/mongo/` builds an initialized MongoDB 7 image. On an empty volume, `init.js` creates
the collections and indexes that `documents/migrations/0001_initial.py` creates, plus the
matching `django_migrations` record, so the app starts without `manage.py migrate`. It
contains no data. Root credentials come from `MONGO_USER` / `MONGO_PASSWORD`, the database
name from `MONGODB_DB`.

The service has one migration, `0001_initial` (both models). No Django contrib app is
installed (no `auth`, `admin`, `sessions`, `contenttypes`), so no other migrations exist.
At run time Django never reads `django_migrations`; only `migrate` does.

`init.js` must change with the migrations. After adding a migration:

1. Run `python manage.py migrate` against an empty MongoDB.
2. List what it created:
   `mongosh <db> --eval 'db.getCollectionNames().forEach(c => printjson(db[c].getIndexes()))'`
3. Copy the new collections and indexes (same names and options) and the new
   `django_migrations` record into `db/mongo/init.js`.
4. Check on an empty volume that `python manage.py migrate --check` exits 0.

To start again from an empty database, remove the volumes: `docker compose down -v`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `JWT_PUBLIC_KEY` | Portal's public key, PEM (required; without it every request is refused) |
| `JWT_ISSUER`, `JWT_AUDIENCE` | Must match the token (`tenant-portal` / `ai-voice-platform`) |
| `JWT_TENANT_CLAIM`, `JWT_ROLE_CLAIM` | Claim names (`tenant_id`, `role`) |
| `JWT_ALLOWED_ROLES` | Comma-separated roles allowed in (default `tenant_admin`; empty = any) |
| `CORS_ALLOWED_ORIGINS` | Browser origins allowed to call the API (empty = none) |
| `MONGODB_URI`, `MONGODB_DB` | Database connection |
| `MAX_UPLOAD_SIZE_MB`, `SESSION_LINK_BASE_URL`, `MEDIA_ROOT` | Uploads and link URLs |

See `.env.example` for the complete list.

## Secrets

No secret is stored in this repository. Real values live only in `.env` on your machine
(git-ignored, created from `.env.example`) and in GitLab CI/CD variables (**Masked**, not
Protected). Never commit `.env`. If a value is ever pushed, treat it as leaked: generate a new
one and replace it everywhere (deleting the file is not enough, it stays in the git history).

| Variable | How to generate | Notes |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `openssl rand -base64 50` | Required when `DJANGO_DEBUG` is off. |
| `MONGO_PASSWORD` | `openssl rand -hex 24` | Root user, used only by the setup job. |
| `MONGO_APP_PASSWORD` | `openssl rand -hex 24` | The app's own user. Letters and digits only (it goes in `MONGODB_URI`). |
| `JWT_PUBLIC_KEY` | Not generated here: it is the tenant-portal's public key | From the portal folder: `awk 'NF {sub(/\r/, ""); printf "%s\\n", $0}' keys/jwt_public.pem`, then paste it in single quotes. When the portal's keys change, update it here too. |

This service never holds a private key. The tests sign their own throwaway JWTs.

## Test

```bash
pip install -r requirements-dev.txt
MONGODB_URI=mongodb://localhost:27017 MONGODB_DB=dev DJANGO_DEBUG=true \
  coverage run manage.py test --noinput && coverage report
ruff check . && ruff format --check .
```

The tests sign their own throwaway JWTs, so no auth service is needed.

## Pipeline

`.gitlab-ci.yml` in this folder is the service's own child pipeline: lint, format,
hadolint, semgrep, trivy (dependencies and image), checkov, tests with a 90% coverage
gate, image build, SBOM, and publish on the default branch. It reads `SERVICE_DIR`
from the parent pipeline (`.` when this folder is a repo of its own).

## Known gaps

- Uploads are stored on local disk (a volume in compose), which conflicts with stateless
  services (NFR-01). Move them to shared object storage later.
- A suspended tenant keeps access until its token expires (up to 15 minutes), because the
  token is trusted without calling the portal.
- This service does not store vectors or embeddings; ingestion for RAG is a separate stage.
