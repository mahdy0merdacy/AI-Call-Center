# Tenant portal (auth service)

Registration, login, MFA and Google sign-in for the AI voice reception platform.
Django 5.2 + Django REST Framework, data in PostgreSQL. It issues RS256 JWTs; other
services (for example `services/documents`) verify them with its public key.

Main endpoints: `POST /api/auth/register/`, `POST /api/auth/login/`,
`GET /api/auth/me/`, `GET /.well-known/jwks.json`, `GET /health/`.

Tests and the CI pipeline are described in [TESTING.md](TESTING.md).

## Run it

```bash
cp .env.example .env          # fill in the values (see Secrets below)
sh scripts/generate_keys.sh   # JWT key pair in keys/ (git-ignored)
docker compose up --build
```

The portal answers on `http://127.0.0.1:8000` (`PORTAL_PORT`), Mailpit on
`http://127.0.0.1:8025`, PostgreSQL on `127.0.0.1:5434` (`POSTGRES_HOST_PORT`; 5433 is
used by the data stack in `data/`).

## Database

`db/postgres/` builds an initialized PostgreSQL 16 image (`pgvector/pgvector:pg16`, the
same image CI uses). On an empty volume, `init.sql` creates every table and fills
`django_migrations`, so the app starts without `manage.py migrate` (compose sets
`RUN_MIGRATIONS=false`). It contains no data. Credentials come from `POSTGRES_DB`,
`POSTGRES_USER` and `POSTGRES_PASSWORD`.

Migrations: one initial migration per app, except `accounts`, which needs two.
`accounts/0002_mfa_devices` holds the proxy models of django-otp devices. They cannot go
in `0001`, because django-otp's tables point to our custom `User`, which `0001` creates
(Django reports a circular dependency).

`init.sql` must change with the migrations. After adding a migration, regenerate it from an
empty database:

```bash
docker run -d --name schema-db -e POSTGRES_DB=tenant_portal -e POSTGRES_USER=portal \
  -e POSTGRES_PASSWORD=schema pgvector/pgvector:pg16
# run against it: python manage.py migrate && python manage.py createcachetable
# (DATABASE_URL=postgres://portal:schema@<host>:5432/tenant_portal)
docker exec schema-db pg_dump -U portal -d tenant_portal --schema-only --no-owner --no-privileges > schema.sql
docker exec schema-db pg_dump -U portal -d tenant_portal --data-only --no-owner --no-privileges \
  --inserts -t django_migrations -t django_migrations_id_seq > migrations.sql
```

Then put both into `db/postgres/init.sql` (keep the header comment, drop any
`\restrict` / `\unrestrict` lines), and check on an empty volume that
`python manage.py migrate --check` exits 0. To start again from an empty database, remove
the volume: `docker compose down -v`.

## Secrets

No secret is stored in this repository. Real values live only in:

- `.env` / `.env.local` on your machine (git-ignored), created from `.env.example`;
- `keys/jwt_private.pem` and `keys/jwt_public.pem` (git-ignored, only `keys/.gitkeep` is tracked);
- GitLab CI/CD variables (Settings > CI/CD > Variables, **Masked**, not Protected).

Never commit `.env`, `.env.local` or anything in `keys/`. If one is ever pushed, treat it as
leaked: generate a new value as below and replace it everywhere (deleting the file is not enough,
it stays in the git history).

### Generate your own values

```bash
cp .env.example .env
```

| Variable / file | How to generate | Notes |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `openssl rand -base64 50` | Changing it logs out every session. |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` | Letters and digits only (it goes in `DATABASE_URL`). |
| `MFA_ENCRYPTION_KEY` | `openssl rand -base64 32` | Base64, at least 32 bytes. Changing it makes existing TOTP secrets and backup codes unreadable, so users must enrol MFA again. |
| `keys/jwt_private.pem`, `keys/jwt_public.pem` | `sh scripts/generate_keys.sh` (add `--force` to replace) | RSA 2048. After a change, every service that verifies tokens needs the new public key (see below), and existing tokens stop working. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud Console > APIs & Services > Credentials > your OAuth client | To rotate, use "Reset secret" (or create a new client) and update `.env`. Leave empty to disable Google sign-in. |
| `EMAIL_HOST_PASSWORD` | From your SMTP provider | Empty with Mailpit in development. |

Give the public key to the documents service (`JWT_PUBLIC_KEY`, one line with `\n`):

```bash
awk 'NF {sub(/\r/, ""); printf "%s\\n", $0}' keys/jwt_public.pem
```

The tests (`config.settings.test`) generate throwaway RSA keys and use fake values, so they
need no secret except the CI variable `TEST_DB_PASSWORD` for the test database.
