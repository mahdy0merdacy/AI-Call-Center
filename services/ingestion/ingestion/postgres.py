"""Access to the document_chunks table (tenant-portal Postgres, pgvector).

The table is created by services/tenant-portal/db/postgres/02-pgvector.sql; this
worker only reads and writes rows, it never changes the schema.
"""

import psycopg


def connect(database_url: str) -> psycopg.Connection:
    conn = psycopg.connect(database_url, connect_timeout=5)
    check_schema(conn)
    return conn


def check_schema(conn: psycopg.Connection) -> None:
    """Fail at startup if pgvector or the table is missing (02-pgvector.sql not applied)."""
    row = conn.execute(
        'SELECT'
        " EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector'),"
        " to_regclass('public.document_chunks') IS NOT NULL"
    ).fetchone()
    conn.rollback()  # read-only check: don't leave a transaction open
    has_vector, has_table = row if row else (False, False)
    if not (has_vector and has_table):
        raise RuntimeError(
            'pgvector or document_chunks is missing: apply '
            'services/tenant-portal/db/postgres/02-pgvector.sql first'
        )
