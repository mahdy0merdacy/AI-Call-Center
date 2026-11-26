"""Retrieval from a platform client's documents, through the knowledge service.

The knowledge service (services/ingestion, `python -m ingestion.api`) holds the
embedding model and runs the hybrid search; this side only asks. One search per
LLM turn, keyed by the client's platform UUID, and the service filters on that
UUID, so a call only ever sees its own client's documents.

The budget is tight (~1.5 s): the caller is waiting in silence. Past it, or on any
error, the turn goes ahead without documents and the model is told the lookup
failed, so it says so instead of guessing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import aiohttp

SEARCH_TIMEOUT_S = 1.5
TOP_K = 4
# A question is "covered" when its nearest chunk is at least this similar
# (bge-m3 cosine). Measured on a real policy document (Stockline's stock control
# policy, 8 + 8 questions): covered 0.490-0.698 (lowest: a bare code, "what is
# SCP-105"), not covered 0.320-0.469. 0.48 separates them; the margin is narrow,
# so recheck per deployment. KNOWLEDGE_COVERED_MIN overrides.
COVERED_MIN = float(os.environ.get("KNOWLEDGE_COVERED_MIN", "0.48"))
MAX_QUERY_CHARS = 2000      # the service refuses longer queries
MAX_PASSAGE_CHARS = 1500    # chunks are ~800 chars; this only bounds the prompt


class KnowledgeError(Exception):
    """No usable answer: timeout, connection or HTTP error, or a malformed reply."""


@dataclass(frozen=True)
class Passage:
    document_id: str
    title: str | None
    section: str | None
    content: str
    score: float


@dataclass(frozen=True)
class SearchResult:
    passages: list[Passage]
    took_ms: int = 0    # the search itself, as the service reports it
    # Cosine similarity of the nearest chunk (0..1), None if the service sent none.
    best_similarity: float | None = None

    def covered(self, threshold: float) -> bool:
        """Do the documents speak about this at all (vs a knowledge gap)?"""
        return bool(self.passages) and (self.best_similarity or 0.0) >= threshold


class KnowledgeClient:
    """POST /v1/search on the knowledge service (KNOWLEDGE_URL)."""

    def __init__(self, base_url: str, *, timeout_s: float = SEARCH_TIMEOUT_S, k: int = TOP_K):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.k = k

    async def search(self, session: aiohttp.ClientSession, tenant_id: str,
                     query: str) -> SearchResult:
        """The k passages that best match `query` in this tenant's documents.

        Raises KnowledgeError on any failure; the caller decides what the model
        is told. Takes the turn's HTTP session rather than holding one, so there
        is nothing to close and nothing tied to an event loop.
        """
        payload = {"tenant_id": tenant_id, "query": query.strip()[:MAX_QUERY_CHARS],
                   "k": self.k}
        try:
            async with session.post(f"{self.base_url}/v1/search", json=payload,
                                    timeout=aiohttp.ClientTimeout(total=self.timeout_s)) as resp:
                if resp.status != 200:
                    # Status only: an error body may echo the query, i.e. the caller.
                    raise KnowledgeError(f"HTTP {resp.status}")
                body = await resp.json()
        except TimeoutError as exc:
            raise KnowledgeError(f"no answer within {self.timeout_s:.1f} s") from exc
        except (aiohttp.ClientError, ValueError) as exc:
            raise KnowledgeError(type(exc).__name__) from exc
        best = body.get("best_similarity")
        return SearchResult(_passages(body)[:self.k], _int(body.get("took_ms")),
                            float(best) if isinstance(best, (int, float)) else None)


def _passages(body: Any) -> list[Passage]:
    chunks = body.get("chunks") if isinstance(body, dict) else None
    if not isinstance(chunks, list):
        raise KnowledgeError("reply has no chunks list")
    out = []
    for c in chunks:
        # Skip what cannot be quoted rather than fail the whole turn on it.
        if not isinstance(c, dict) or not isinstance(c.get("content"), str):
            continue
        content = c["content"].strip()
        if not content:
            continue
        out.append(Passage(
            document_id=str(c.get("document_id") or ""),
            title=_text(c.get("document_title")),
            section=_text(c.get("section")),
            content=content[:MAX_PASSAGE_CHARS],
            score=_float(c.get("score")),
        ))
    return out


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0
