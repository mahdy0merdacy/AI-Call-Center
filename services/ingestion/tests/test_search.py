"""Hybrid search without Postgres: RRF fusion, and what both queries SEND.

The rule that matters most: every query filters on the requested tenant AND the
embedding model, so a tenant can never get another tenant's chunks.
"""

import re
import uuid

import numpy as np
import pytest

from ingestion import search
from ingestion.search import (
    CANDIDATES,
    TEXT_SQL,
    VECTOR_SQL,
    Hit,
    has_iterative_scans,
    rrf_fuse,
    search_chunks,
)

TENANT = uuid.UUID('cfc05ea1-3de6-4b6c-a963-47343cb10651')
MODEL = 'BAAI/bge-m3'


def hit(chunk_id: int) -> Hit:
    return Hit(chunk_id, f'doc-{chunk_id}', 'Stock Policy', 'Records', f'text {chunk_id}')


def ids(fused):
    return [h.chunk_id for h, _ in fused]


# ---------------------------------------------------------------- fusion


def test_rrf_scores_are_the_sum_of_one_over_60_plus_rank():
    fused = {h.chunk_id: s for h, s in rrf_fuse([[hit(1), hit(2)], [hit(2), hit(3)]], 10)}
    assert fused[1] == pytest.approx(1 / 61)
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[3] == pytest.approx(1 / 62)


def test_found_by_both_rankings_beats_first_place_in_one():
    by_meaning = [hit(1), hit(2), hit(3)]
    by_words = [hit(9), hit(3)]
    # 3: 1/63 + 1/62 beats 1: 1/61 and 9: 1/61.
    assert ids(rrf_fuse([by_meaning, by_words], 10)) == [3, 1, 9, 2]


def test_returns_only_the_best_k():
    assert ids(rrf_fuse([[hit(i) for i in range(1, 21)]], 4)) == [1, 2, 3, 4]


def test_ties_keep_the_order_of_first_appearance():
    # 1 and 7 are both first of one ranking: same score, the ranking listed first wins.
    assert ids(rrf_fuse([[hit(1)], [hit(7)]], 10)) == [1, 7]
    assert ids(rrf_fuse([[hit(7)], [hit(1)]], 10)) == [7, 1]


def test_a_chunk_counts_once_per_ranking():
    fused = rrf_fuse([[hit(1), hit(1), hit(2)]], 10)
    assert ids(fused) == [1, 2]
    assert fused[0][1] == pytest.approx(1 / 61)


def test_empty_rankings_give_nothing():
    assert rrf_fuse([[], []], 4) == []


def test_text_only_or_meaning_only_still_answers():
    assert ids(rrf_fuse([[], [hit(5), hit(6)]], 4)) == [5, 6]
    assert ids(rrf_fuse([[hit(5), hit(6)], []], 4)) == [5, 6]


# ---------------------------------------------------------------- SQL


@pytest.mark.parametrize('sql', [VECTOR_SQL, TEXT_SQL], ids=['by_meaning', 'by_words'])
def test_both_queries_filter_on_tenant_and_model(sql):
    where = sql.split(' WHERE ', 1)[1].split(' ORDER BY ', 1)[0]
    assert 'tenant_id = %(tenant_id)s' in where
    assert 'embedding_model = %(model)s' in where
    # Joined with AND (an OR would let other tenants' rows through).
    assert not re.search(r'\bOR\b', where, re.IGNORECASE)


def test_vector_query_is_ordered_by_distance_only():
    # A second sort key would stop Postgres from using the hnsw index.
    assert VECTOR_SQL.split(' ORDER BY ', 1)[1].startswith('embedding <=> %(vector)s LIMIT')


def test_text_query_uses_the_simple_config_like_the_index():
    assert "websearch_to_tsquery('simple', %(query)s)" in TEXT_SQL
    assert 'content_tsv @@' in TEXT_SQL


class FakeConnection:
    """Records each query and answers with canned rows, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        rows = self.answers.pop(0)

        class Result:
            def fetchall(self):
                return rows

            def fetchone(self):
                return rows[0] if rows else None

        return Result()


def row(chunk_id):
    return (chunk_id, f'doc-{chunk_id}', 'Stock Policy', 'Records', f'text {chunk_id}')


def test_search_runs_both_queries_for_the_requested_tenant_and_model():
    conn = FakeConnection([row(1), row(2)], [row(2), row(3)])
    vector = np.ones(1024, dtype=np.float32)

    found = search_chunks(conn, TENANT, MODEL, vector, 'stock records', 2)

    (vector_sql, vector_params), (text_sql, text_params) = conn.calls
    assert (vector_sql, text_sql) == (VECTOR_SQL, TEXT_SQL)
    for params in (vector_params, text_params):
        assert params['tenant_id'] == TENANT
        assert params['model'] == MODEL
        assert params['limit'] == CANDIDATES
    assert vector_params['vector'] is vector
    assert text_params['query'] == 'stock records'
    # Fused: 2 is in both rankings.
    assert ids(found) == [2, 1]
    assert found[0][0] == Hit(2, 'doc-2', 'Stock Policy', 'Records', 'text 2')


def test_a_chunk_found_by_both_rankings_keeps_its_similarity():
    """Fusion keeps the words-ranking Hit for a chunk found by both; the cosine
    similarity the meaning ranking measured must survive, it is the coverage signal."""
    vector_rows = [(*row(1), 0.82), (*row(2), 0.64)]     # meaning: with similarity
    conn = FakeConnection(vector_rows, [row(2), row(5)])    # words: none
    found = search_chunks(conn, TENANT, MODEL, np.ones(1024, dtype=np.float32), 'records', 3)
    by_id = {hit.chunk_id: hit.similarity for hit, _ in found}
    assert by_id == {2: 0.64, 1: 0.82, 5: None}


def test_the_vector_query_returns_cosine_similarity():
    assert '1 - (embedding <=> %(vector)s) AS similarity' in VECTOR_SQL


def test_an_exact_tie_goes_to_the_words_ranking():
    # 1: first by meaning only, 9: first by words only. Both score 1/61.
    conn = FakeConnection([row(1)], [row(9)])
    found = search_chunks(conn, TENANT, MODEL, np.ones(1024, dtype=np.float32), 'SCP-107', 2)
    assert ids(found) == [9, 1]


# ---------------------------------------------------------------- connection setup


@pytest.mark.parametrize(
    ('version', 'expected'),
    [
        ('0.6.2', False),
        ('0.7.4', False),
        ('0.8.0', True),
        ('0.8.1', True),
        ('0.10.0', True),
        ('1.0.0', True),
        ('?', False),
    ],
)
def test_iterative_scans_need_pgvector_0_8(version, expected):
    assert has_iterative_scans(version) is expected


@pytest.mark.parametrize(('version', 'iterative'), [('0.8.1', True), ('0.6.2', False)])
def test_each_connection_gets_a_short_timeout_and_iterative_scans(monkeypatch, version, iterative):
    monkeypatch.setattr(search, 'register_vector', lambda conn: None)
    conn = FakeConnection([], [(version,)], [])

    search._configure(conn)

    sql = [s for s, _ in conn.calls]
    assert "SET statement_timeout = '2s'" in sql
    assert ("SET hnsw.iterative_scan = 'strict_order'" in sql) is iterative
