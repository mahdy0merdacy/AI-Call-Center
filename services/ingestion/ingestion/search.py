"""Hybrid search over ONE tenant's chunks, for live calls (knowledge service).

Two rankings of the same tenant's chunks, then one fused list:

    by meaning   top 20 by cosine distance  embedding <=> question vector  (hnsw index)
    by words     top 20 by ts_rank_cd       content_tsv @@ question words  (gin index)
    fusion       Reciprocal Rank Fusion: score = sum of 1 / (60 + rank) over the rankings

Meaning finds "how long are stock records kept?" -> "Records are kept for 10 years";
words find exact codes and names ("SCP-101") that vectors blur. RRF uses only ranks,
so the two very different scores never have to be compared.

RULE: BOTH queries filter on tenant_id AND embedding_model. A tenant's search can
never return another tenant's chunks, nor compare vectors made by another model.
"""

import logging
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from .postgres import DEAD_PEER

log = logging.getLogger('ingestion')

# Rows each ranking brings to the fusion.
CANDIDATES = 20
# The constant of the original RRF paper (Cormack et al., 2009). It flattens the
# top ranks, so one ranking alone cannot decide the result.
RRF_CONSTANT = 60
# How long a search waits for a free connection before giving up (503).
POOL_TIMEOUT = 2.0

# Ordered by distance ONLY: with a second sort key Postgres could no longer read
# the order from the hnsw index, and would compare the question with every row.
VECTOR_SQL = (
    'SELECT id, document_id::text, document_title, section, content,'
    ' 1 - (embedding <=> %(vector)s) AS similarity'
    ' FROM document_chunks'
    ' WHERE tenant_id = %(tenant_id)s AND embedding_model = %(model)s'
    ' ORDER BY embedding <=> %(vector)s'
    ' LIMIT %(limit)s'
)

# 'simple', like the content_tsv column (03-rag-columns.sql): the question's words
# must be normalized the same way as the chunks' words, or they never match.
# websearch_to_tsquery never fails on what a caller says ("quotes", or, -word).
TEXT_SQL = (
    'SELECT id, document_id::text, document_title, section, content'
    " FROM document_chunks, websearch_to_tsquery('simple', %(query)s) AS words"
    ' WHERE tenant_id = %(tenant_id)s AND embedding_model = %(model)s'
    ' AND content_tsv @@ words'
    ' ORDER BY ts_rank_cd(content_tsv, words) DESC, id'
    ' LIMIT %(limit)s'
)


@dataclass(frozen=True)
class Hit:
    """One chunk, as both ranking queries return it."""

    chunk_id: int
    document_id: str
    document_title: str | None
    section: str | None
    content: str
    # Cosine similarity to the question (0..1), from the meaning ranking only.
    # Fused RRF scores are relative; this is the absolute "is it even about
    # that?" signal that tells a covered question from a knowledge gap.
    similarity: float | None = None


def rrf_fuse(
    rankings: Iterable[Sequence[Hit]], limit: int, constant: int = RRF_CONSTANT
) -> list[tuple[Hit, float]]:
    """Fuse rankings (each best first) and return the best `limit` (hit, score).

    A chunk scores the sum of 1 / (constant + rank) over the rankings it is in
    (rank 1 = best), so a chunk found by both queries beats one found by a single
    query at the same rank. Ties keep the order of first appearance: same input,
    same output.
    """
    scores: dict[int, float] = {}
    hits: dict[int, Hit] = {}
    for ranking in rankings:
        seen: set[int] = set()
        for rank, hit in enumerate(ranking, start=1):
            if hit.chunk_id in seen:
                continue  # counted once per ranking
            seen.add(hit.chunk_id)
            hits.setdefault(hit.chunk_id, hit)
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (constant + rank)
    best = sorted(scores, key=lambda chunk_id: -scores[chunk_id])  # stable sort
    return [(hits[chunk_id], scores[chunk_id]) for chunk_id in best[:limit]]


def search_chunks(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    model: str,
    vector: np.ndarray,
    query: str,
    k: int,
) -> list[tuple[Hit, float]]:
    """Run both rankings for ONE tenant and fuse them: the best k (hit, score)."""
    params = {
        'tenant_id': tenant_id,
        'model': model,
        'vector': vector,
        'query': query,
        'limit': CANDIDATES,
    }
    by_meaning = [Hit(*row) for row in conn.execute(VECTOR_SQL, params).fetchall()]
    by_words = [Hit(*row) for row in conn.execute(TEXT_SQL, params).fetchall()]
    # Words first, so they win exact ties: a words hit contains EVERY word of the
    # question, while the vector ranking always returns its 20 nearest, relevant or not.
    fused = rrf_fuse([by_words, by_meaning], k)
    # A chunk found by both rankings keeps its words-ranking Hit: give it back
    # the similarity the meaning ranking measured.
    similarity = {hit.chunk_id: hit.similarity for hit in by_meaning}
    return [(replace(hit, similarity=similarity.get(hit.chunk_id, hit.similarity)), score)
            for hit, score in fused]


def has_iterative_scans(pgvector_version: str) -> bool:
    """pgvector 0.8.0 added iterative index scans."""
    match = re.match(r'(\d+)\.(\d+)', pgvector_version)
    return bool(match) and (int(match[1]), int(match[2])) >= (0, 8)


def _configure(conn: psycopg.Connection) -> None:
    """Prepare each new connection of the pool (autocommit: nothing left open)."""
    # numpy arrays <-> pgvector "vector" values. Fails if pgvector is missing.
    register_vector(conn)
    # A caller is waiting on the line: a slow search is worse than no search.
    conn.execute("SET statement_timeout = '2s'")
    # When Postgres reads the hnsw index (a large tenant), the index returns the
    # ~40 nearest rows of ALL tenants and the tenant filter comes after: it can
    # leave fewer than 20 rows. Iterative scans read on until 20 rows pass.
    row = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    version = row[0] if row else '?'
    if has_iterative_scans(version):
        conn.execute("SET hnsw.iterative_scan = 'strict_order'")
    else:
        log.warning('pgvector %s has no iterative index scans: fewer rows possible', version)


class ChunkStore:
    """Read-only access to document_chunks for searches (role voice_reader).

    A few connections are kept open and reused: opening one costs more than a
    search, and a caller asks a question every few seconds. The pool connects in
    the background and replaces broken connections by itself, so Postgres can
    start after this service, or restart, without restarting it.
    """

    def __init__(self, database_url: str) -> None:
        self._pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={'autocommit': True, 'connect_timeout': 3, **DEAD_PEER},
            configure=_configure,
            # Tested when taken from the pool: a connection broken by a Postgres
            # restart is replaced instead of failing a caller's search.
            check=ConnectionPool.check_connection,
            # Give up a failing connection attempt after 30 s (default 5 min): the
            # next request then starts a fresh one, right away once Postgres is back.
            reconnect_timeout=30,
            name='knowledge',
        )

    def open(self) -> None:
        self._pool.open(wait=False)  # never blocks startup: Postgres may not be up yet

    def close(self) -> None:
        self._pool.close()

    def ping(self) -> None:
        """Raise unless this role can read document_chunks right now."""
        with self._pool.connection(timeout=POOL_TIMEOUT) as conn:
            # LIMIT 0 reads no row, but Postgres still checks that the table exists
            # and that this role may read it (the db-grants job has run).
            conn.execute('SELECT 1 FROM document_chunks LIMIT 0')

    def search(
        self, tenant_id: uuid.UUID, model: str, vector: np.ndarray, query: str, k: int
    ) -> list[tuple[Hit, float]]:
        with self._pool.connection(timeout=POOL_TIMEOUT) as conn:
            return search_chunks(conn, tenant_id, model, vector, query, k)
