# PostgreSQL 16 for the tenant-portal, initialized with the app's schema.
# Base: the official postgres:16 image plus the pgvector extension (same image as
# the CI test service). The extension is available but not enabled by init.sql.
# init.sql runs once, when the data volume is empty: it creates every table
# and fills django_migrations, so the app starts without `manage.py migrate`.
# Credentials come from the environment at run time:
#   POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
FROM pgvector/pgvector:pg16

COPY --chmod=644 init.sql /docker-entrypoint-initdb.d/01-init.sql
