"""Access to the document_chunks table (tenant-portal Postgres, pgvector).

The table is created by services/tenant-portal/db/postgres/02-pgvector.sql and
03-rag-columns.sql; this worker only reads and writes rows, it never changes the
schema.
"""

import hashlib

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from .chunking import Chunk
from .cleaning import to_uuid


def connect(database_url: str) -> psycopg.Connection:
    # autocommit=True: no transaction is left open between documents. Writes that
    # must succeed or fail together use an explicit `with conn.transaction():`.
    conn = psycopg.connect(database_url, connect_timeout=5, autocommit=True)
    check_schema(conn)
    # Teach psycopg the `vector` type: numpy arrays are sent as pgvector values.
    # Done after check_schema, because it needs the extension to exist.
    register_vector(conn)
    return conn


def check_schema(conn: psycopg.Connection) -> None:
    """Fail at startup if pgvector, the table or the step 1 columns are missing."""
    row = conn.execute(
        'SELECT'
        " EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector'),"
        " to_regclass('public.document_chunks') IS NOT NULL,"
        ' EXISTS (SELECT 1 FROM information_schema.columns'
        "         WHERE table_name = 'document_chunks' AND column_name = 'content_hash')"
    ).fetchone()
    has_vector, has_table, has_rag_columns = row if row else (False, False, False)
    if not (has_vector and has_table):
        raise RuntimeError(
            'pgvector or document_chunks is missing: apply '
            'services/tenant-portal/db/postgres/02-pgvector.sql first'
        )
    if not has_rag_columns:
        raise RuntimeError(
            'document_chunks has no content_hash column: apply '
            'services/tenant-portal/db/postgres/03-rag-columns.sql first'
        )


def content_hash(content: str) -> str:
    """sha256 fingerprint of a chunk's text (same text -> same 64-char hex)."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def replace_chunks(
    conn: psycopg.Connection,
    doc: dict,
    chunks: list[Chunk],
    vectors: np.ndarray,
    model_name: str,
) -> int:
    """Replace ALL rows of one document with `chunks`, in ONE transaction.

    doc is the Mongo record (its "id" and "tenant_id" are 32-hex strings).
    vectors[i] is the embedding of chunks[i].
    Returns how many rows were inserted (after skipping duplicates).

    If anything fails (connection lost, bad value...), the transaction is rolled
    back: the document keeps exactly the rows it had before, never half of them.
    """
    if len(chunks) != len(vectors):
        raise ValueError(f'{len(chunks)} chunks but {len(vectors)} vectors')

    # Raises ValueError on a broken id: nothing is written under a wrong tenant.
    tenant_id = to_uuid(doc['tenant_id'])
    document_id = to_uuid(doc['id'])

    # Skip duplicate chunks inside this document (same text = same hash), e.g. a
    # paragraph that the PDF repeats on two pages. chunk_index stays 0, 1, 2...
    # with no gaps, in document order.
    rows = []
    seen: set[str] = set()
    for chunk, vector in zip(chunks, vectors, strict=True):
        digest = content_hash(chunk.content)
        if digest in seen:
            continue
        seen.add(digest)
        rows.append(
            (
                tenant_id,
                document_id,
                chunk.document_title,
                chunk.section,
                len(rows),  # chunk_index
                chunk.content,
                chunk.codes,
                vector,
                model_name,
                digest,
            )
        )

    with conn.transaction():
        # Both ids in the filter: even if two tenants ever had the same document
        # id, we can only ever delete THIS tenant's rows (decision 12).
        conn.execute(
            'DELETE FROM document_chunks WHERE document_id = %s AND tenant_id = %s',
            (document_id, tenant_id),
        )
        with conn.cursor() as cur:
            cur.executemany(
                'INSERT INTO document_chunks'
                ' (tenant_id, document_id, document_title, section, chunk_index,'
                '  content, codes, embedding, embedding_model, content_hash)'
                ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                rows,
            )
    return len(rows)
