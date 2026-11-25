"""replace_chunks() with a fake connection (no Postgres needed).

Checks what it SENDS to Postgres: one transaction, delete filtered by document
AND tenant, uuid ids, duplicates skipped, chunk_index without gaps.
"""

import uuid
from contextlib import contextmanager

import numpy as np
import pytest

from ingestion.chunking import Chunk
from ingestion.postgres import content_hash, replace_chunks

DOC = {'id': '5c3351a8e26a4d1586c9f5960048c462', 'tenant_id': 'cfc05ea13de64b6ca96347343cb10651'}


class FakeConnection:
    """Records every SQL statement, and whether it ran inside a transaction."""

    def __init__(self):
        self.calls = []  # (in_transaction, sql, params)
        self.in_transaction = False

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False

    def execute(self, sql, params=None):
        self.calls.append((self.in_transaction, sql, params))

    @contextmanager
    def cursor(self):
        conn = self

        class Cursor:
            def executemany(self, sql, rows):
                conn.calls.append((conn.in_transaction, sql, list(rows)))

        yield Cursor()


def chunk(section, body, codes=()):
    return Chunk('Stock Policy', section, f'[Stock Policy > {section}]\n{body}', list(codes))


def test_deletes_then_inserts_in_one_transaction_with_uuid_ids():
    conn = FakeConnection()
    chunks = [chunk('1. Records', 'Kept 10 years.'), chunk('2. Theft', 'Termination.', ['SCP-102'])]
    stored = replace_chunks(conn, DOC, chunks, np.zeros((2, 1024)), 'BAAI/bge-m3')

    assert stored == 2
    (in_tx_delete, delete_sql, delete_params), (in_tx_insert, insert_sql, rows) = conn.calls
    assert in_tx_delete and in_tx_insert  # both inside the same transaction
    assert delete_sql.startswith('DELETE') and 'tenant_id' in delete_sql
    assert delete_params == (uuid.UUID(DOC['id']), uuid.UUID(DOC['tenant_id']))
    assert insert_sql.startswith('INSERT')

    tenant_id, document_id, title, section, index, content, codes, _vec, model, digest = rows[1]
    assert tenant_id == uuid.UUID('cfc05ea1-3de6-4b6c-a963-47343cb10651')
    assert document_id == uuid.UUID('5c3351a8-e26a-4d15-86c9-f5960048c462')
    assert (title, section, index, codes, model) == (
        'Stock Policy',
        '2. Theft',
        1,
        ['SCP-102'],
        'BAAI/bge-m3',
    )
    assert digest == content_hash(content)


def test_skips_duplicate_chunks_and_keeps_chunk_index_without_gaps():
    conn = FakeConnection()
    a, b = chunk('1. Records', 'Kept 10 years.'), chunk('2. Theft', 'Termination.')
    stored = replace_chunks(conn, DOC, [a, a, b], np.zeros((3, 1024)), 'BAAI/bge-m3')

    rows = conn.calls[1][2]
    assert stored == 2
    assert [(r[3], r[4]) for r in rows] == [('1. Records', 0), ('2. Theft', 1)]


def test_refuses_a_broken_tenant_id_before_writing_anything():
    conn = FakeConnection()
    with pytest.raises(ValueError):
        replace_chunks(
            conn, {**DOC, 'tenant_id': 'maison-verte'}, [chunk('1', 'x')], np.zeros((1, 1024)), 'm'
        )
    assert conn.calls == []  # nothing sent to Postgres


def test_refuses_a_different_number_of_chunks_and_vectors():
    with pytest.raises(ValueError, match='2 chunks but 1 vectors'):
        replace_chunks(
            FakeConnection(), DOC, [chunk('1', 'a'), chunk('2', 'b')], np.zeros((1, 1024)), 'm'
        )
