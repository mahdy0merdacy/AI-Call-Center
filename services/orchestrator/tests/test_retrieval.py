"""Retrieval for platform clients: the knowledge client, and what the model is told.

The client runs against a real HTTP server on localhost, because timeouts and
broken replies are what it exists to survive. The agent tests capture the
messages sent to the LLM instead of calling one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from app.agent import Agent, retrieval_prompt
from app.knowledge import KnowledgeClient, KnowledgeError, Passage, SearchResult
from app.tenants import PlatformTenant, platform_tenant
from app.tools import ToolRegistry

TENANT_ID = "6f1c9a52-8d0e-4b7a-9c43-2b1e0f5d7a11"
HOURS = {"document_id": "d1", "document_title": "Opening hours", "section": "Weekdays",
         "content": "We are open 9 to 6, Monday to Friday.", "score": 0.031}


# ── the knowledge client ─────────────────────────────────────────────────────


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _search(handler: Handler, query: str = "when are you open?",
            timeout_s: float = 1.5) -> SearchResult:
    """One search against a local server whose /v1/search is `handler`."""
    async def run() -> SearchResult:
        app = web.Application()
        app.router.add_post("/v1/search", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            client = KnowledgeClient(f"http://127.0.0.1:{port}/", timeout_s=timeout_s)
            async with aiohttp.ClientSession() as session:
                return await client.search(session, TENANT_ID, query)
        finally:
            await runner.cleanup()

    return asyncio.run(run())


def test_search_sends_the_tenant_query_and_k_and_reads_the_passages():
    seen: dict[str, Any] = {}

    async def handler(request: web.Request) -> web.Response:
        seen.update(await request.json())
        return web.json_response({"chunks": [HOURS], "took_ms": 42})

    result = _search(handler, query="  when are you open?  ")
    assert seen == {"tenant_id": TENANT_ID, "query": "when are you open?", "k": 4}
    assert result == SearchResult([Passage("d1", "Opening hours", "Weekdays",
                                           HOURS["content"], 0.031)], took_ms=42)


def test_search_keeps_queries_within_what_the_service_accepts():
    seen: dict[str, Any] = {}

    async def handler(request: web.Request) -> web.Response:
        seen.update(await request.json())
        return web.json_response({"chunks": [], "took_ms": 1})

    _search(handler, query="x" * 5000)
    assert len(seen["query"]) == 2000


def test_search_skips_chunks_it_cannot_quote():
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"chunks": [
            {"content": None}, "junk", {"content": "   "},
            {"document_id": "d2", "document_title": None, "section": "",
             "content": "Parking is free.", "score": "high"},
        ]})

    (only,) = _search(handler).passages
    assert (only.title, only.section, only.content, only.score) == \
        (None, None, "Parking is free.", 0.0)


@pytest.mark.parametrize("reply", [
    web.Response(status=503, text='{"status": "starting"}'),
    web.Response(status=400, text='{"detail": "bad query"}'),
    web.Response(status=200, text="not json", content_type="application/json"),
    web.json_response({"passages": []}),
])
def test_search_fails_loudly_on_a_bad_reply(reply):
    async def handler(request: web.Request) -> web.Response:
        return reply

    with pytest.raises(KnowledgeError):
        _search(handler)


def test_search_gives_up_at_its_budget():
    async def slow(request: web.Request) -> web.Response:
        await asyncio.sleep(0.5)        # the server's shutdown waits this out
        return web.json_response({"chunks": []})

    with pytest.raises(KnowledgeError, match="no answer within 0.1 s"):
        _search(slow, timeout_s=0.1)


def test_search_with_the_service_down_is_an_error():
    async def run() -> None:
        client = KnowledgeClient("http://127.0.0.1:9")      # discard port: refused
        async with aiohttp.ClientSession() as session:
            await client.search(session, TENANT_ID, "hello")

    with pytest.raises(KnowledgeError):
        asyncio.run(run())


# ── what the model is told ───────────────────────────────────────────────────


def test_hits_are_quoted_with_their_source_and_bind_the_answer():
    text = retrieval_prompt("Bistro Lumière", SearchResult([
        Passage("d1", "Opening hours", "Weekdays", "Open 9 to 6.", 0.03),
        Passage("d2", None, None, "Parking is free.", 0.02),
    ]))
    assert text.startswith("Reference material from Bistro Lumière's documents")
    assert "Answer only from it." in text
    assert "[1] Opening hours — Weekdays\nOpen 9 to 6." in text
    assert "[2] Untitled document\nParking is free." in text


def test_no_hits_means_the_documents_do_not_cover_it():
    text = retrieval_prompt("Bistro Lumière", SearchResult([]))
    assert "documents have nothing on what the caller just said" in text
    assert "say the documents don't cover that" in text
    assert "Do not answer from general knowledge or guess." in text


def test_a_failed_search_means_say_so_and_do_not_guess():
    text = retrieval_prompt("Bistro Lumière", None)
    assert "unavailable right now" in text
    assert "do not guess" in text


# ── through a turn ───────────────────────────────────────────────────────────


class FakeKnowledge(KnowledgeClient):
    def __init__(self, result: SearchResult | None):
        super().__init__("http://knowledge.invalid")
        self.result = result
        self.queries: list[tuple[str, str]] = []

    async def search(self, session, tenant_id, query):
        self.queries.append((tenant_id, query))
        if self.result is None:
            raise KnowledgeError("no answer within 1.5 s")
        return self.result


BISTRO = platform_tenant(PlatformTenant(id=TENANT_ID, name="Bistro Lumière", slug="bistro",
                                        is_active=True))


def _turn(monkeypatch, tenant, knowledge, text="when are you open?"):
    """Run one open turn; return (system prompt sent to the LLM, events)."""
    sent: list[list[dict]] = []

    async def fake_llm(self, session, messages):
        sent.append(messages)
        self.llm_stats = {"ttft_ms": 1, "prompt_tokens": 0, "completion_tokens": 0}
        yield "We are open nine to six on weekdays."

    monkeypatch.setattr(Agent, "_stream_llm", fake_llm)
    agent = Agent(api_key="", model="test", tenant=tenant,
                  tools=ToolRegistry(servers={}, allowed=set()), knowledge=knowledge)
    events: list[dict] = []
    asyncio.run(agent.respond(text, events.append, lambda _: None))
    (messages,) = sent
    return messages[0]["content"], events


def test_a_platform_turn_answers_from_the_passages_found_for_it(monkeypatch):
    knowledge = FakeKnowledge(SearchResult([Passage("d1", "Opening hours", None,
                                                    "Open 9 to 6.", 0.03)], took_ms=12))
    system, events = _turn(monkeypatch, BISTRO, knowledge)
    assert knowledge.queries == [(TENANT_ID, "when are you open?")]
    assert system.startswith(BISTRO.persona)
    assert "[1] Opening hours\nOpen 9 to 6." in system
    (retrieval,) = [e for e in events if e["type"] == "retrieval"]
    assert (retrieval["ok"], retrieval["hits"], retrieval["took_ms"]) == (True, 1, 12)
    assert retrieval["turn"] == 1 and retrieval["latency_ms"] >= 0


def test_a_platform_turn_with_the_search_down_is_told_not_to_guess(monkeypatch):
    system, events = _turn(monkeypatch, BISTRO, FakeKnowledge(None))
    assert retrieval_prompt("Bistro Lumière", None) in system
    (retrieval,) = [e for e in events if e["type"] == "retrieval"]
    assert retrieval["ok"] is False and "1.5 s" in retrieval["error"]


def test_a_platform_turn_without_a_knowledge_service_is_told_not_to_guess(monkeypatch):
    system, _ = _turn(monkeypatch, BISTRO, knowledge=None)
    assert retrieval_prompt("Bistro Lumière", None) in system


def test_a_platform_turn_with_no_match_is_told_the_documents_do_not_cover_it(monkeypatch):
    system, _ = _turn(monkeypatch, BISTRO, FakeKnowledge(SearchResult([])))
    assert retrieval_prompt("Bistro Lumière", SearchResult([])) in system


def _system_before_retrieval(tenant, extra: str = "") -> str:
    """Agent._system without retrieval, verbatim."""
    parts = [tenant.persona.strip()]
    parts.append(
        "You are speaking out loud on a phone call. Keep replies to one or two "
        "short sentences. No markdown, no lists, no emoji. Say every date in "
        "words, exactly as the data below gives it; never write a date as digits."
    )
    if tenant.guardrails:
        parts.append("Rules you must follow:\n" +
                     "\n".join(f"- {g}" for g in tenant.guardrails))
    if tenant.knowledge_text:
        parts.append(
            "Reference material. Answer only from this; if it is not here, say so.\n"
            + tenant.knowledge_text
        )
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def test_a_test_tenant_keeps_its_knowledge_in_the_prompt_and_never_searches(
        monkeypatch, tenant):
    knowledge = FakeKnowledge(SearchResult([]))
    system, events = _turn(monkeypatch, tenant, knowledge, text="what is the weather like")
    assert knowledge.queries == []
    assert not [e for e in events if e["type"] == "retrieval"]
    # The open-turn guard follows the unchanged prompt, and nothing else does.
    before = _system_before_retrieval(tenant) + "\n\n"
    assert system.startswith(before)
    assert system[len(before):].startswith("IMPORTANT: you have not looked anything up.")
    assert "Opening hours: 9 to 5." in system


def test_the_demo_tenants_prompts_are_unchanged():
    from app.flow import TENANTS_DIR, load_tenants

    for t in load_tenants(TENANTS_DIR).values():
        agent = Agent(api_key="", model="test", tenant=t,
                      tools=ToolRegistry(servers={}, allowed=set()))
        for extra in ("", "The tool returned this data: {}"):
            assert agent._system(extra) == _system_before_retrieval(t, extra), t.slug


# ── knowledge coverage: what the Insights page's knowledge gaps are made of ──────


def test_search_reads_the_nearest_chunks_similarity():
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"chunks": [HOURS], "took_ms": 5, "best_similarity": 0.68})

    assert _search(handler).best_similarity == 0.68


def test_covered_means_close_enough_and_something_found():
    hours = [Passage("d1", "Opening hours", None, "Open 9 to 6.", 0.03)]
    assert SearchResult(hours, best_similarity=0.62).covered(0.5)
    assert not SearchResult(hours, best_similarity=0.38).covered(0.5)    # off-topic
    assert not SearchResult([], best_similarity=0.9).covered(0.5)        # nothing to quote
    assert not SearchResult(hours).covered(0.5)                          # service sent none


def test_a_platform_turn_records_what_it_searched_and_whether_it_was_covered(monkeypatch):
    knowledge = FakeKnowledge(SearchResult(
        [Passage("d1", "Opening hours", None, "Open 9 to 6.", 0.03),
         Passage("d2", "Opening hours", None, "Closed Sundays.", 0.02)],
        took_ms=7, best_similarity=0.71))
    _, events = _turn(monkeypatch, BISTRO, knowledge, text="are you open on sunday?")
    (retrieval,) = [e for e in events if e["type"] == "retrieval"]
    assert retrieval["query"] == "are you open on sunday?"
    assert retrieval["best_similarity"] == 0.71 and retrieval["covered"] is True
    assert retrieval["titles"] == ["Opening hours"]          # once per document


def test_a_failed_search_is_recorded_as_not_covered(monkeypatch):
    _, events = _turn(monkeypatch, BISTRO, FakeKnowledge(None))
    (retrieval,) = [e for e in events if e["type"] == "retrieval"]
    assert retrieval["covered"] is False and retrieval["titles"] == []
