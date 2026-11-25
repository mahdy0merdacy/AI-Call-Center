-- voice_reader: the role the voice side (orchestrator, knowledge) reads Postgres
-- with. It may read tenant identities and document chunks, nothing else: no
-- users, no password hashes, no audit log.
--
-- Run by the db-grants job (deploy/compose.yaml; on k3s a Job) as the database
-- owner, on every start. Idempotent: privileges are revoked and granted again
-- in one transaction, so the result is always exactly the set below, a password
-- change in deploy/.env applies on the next run, and a running query never sees
-- the role without its grants.
--
--   psql -f grants.sql     with PGHOST/PGUSER/PGPASSWORD/PGDATABASE and
--                          VOICE_READER_PASSWORD in the environment

\set ON_ERROR_STOP on
\getenv voice_reader_password VOICE_READER_PASSWORD

\if :{?voice_reader_password}
\else
DO $$ BEGIN RAISE EXCEPTION 'VOICE_READER_PASSWORD is not set'; END $$;
\endif

BEGIN;

SELECT 'CREATE ROLE voice_reader'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'voice_reader')
\gexec

ALTER ROLE voice_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    PASSWORD :'voice_reader_password';

-- Start from nothing, so a privilege granted by hand earlier does not linger.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM voice_reader;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM voice_reader;

GRANT CONNECT ON DATABASE :"DBNAME" TO voice_reader;
GRANT USAGE ON SCHEMA public TO voice_reader;
-- Column-level: every query on accounts_tenant must name these columns only.
GRANT SELECT (id, name, slug, is_active) ON accounts_tenant TO voice_reader;
GRANT SELECT ON document_chunks TO voice_reader;

COMMIT;

\echo 'voice_reader: SELECT (id, name, slug, is_active) ON accounts_tenant, SELECT ON document_chunks'
