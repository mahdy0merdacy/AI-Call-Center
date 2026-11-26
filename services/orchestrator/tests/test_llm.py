"""predator first, Groq when predator cannot answer (app/llm.py).

The LLMs are real HTTP servers on localhost speaking the OpenAI streaming
format, so a turn goes through the same aiohttp code a live call does. "Down" is
a port nobody listens on; "slow" is a server that waits before its first token.
"""

from __future__ import annotations

import asyncio
import json
import socket

import aiohttp
import pytest
from aiohttp import web

from app.agent import Agent
from app.llm import GROQ_MODEL, GROQ_URL, LlmBackend, LlmRouter
from app.server import llm_router, parse_args
from app.tools import ToolRegistry

OPEN_TURN = "what is the weather like"     # no flow matches: the LLM answers


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sse(*tokens: str) -> bytes:
    chunks: list[dict[str, object]] = [{"choices": [{"delta": {"content": t}}]} for t in tokens]
    chunks.append({"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 3}})
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) \
        + b"data: [DONE]\n\n"


class FakeLlm:
    """An OpenAI-compatible endpoint: replies with `tokens`, after `delay_s`,
    or with HTTP `status` instead."""

    def __init__(self, *tokens: str, delay_s: float = 0.0, status: int = 200,
                 fail_after_first: bool = False):
        self.tokens, self.delay_s, self.status = tokens, delay_s, status
        self.fail_after_first = fail_after_first
        self.requests: list[dict] = []
        self.url = ""
        self._runner: web.AppRunner | None = None

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(await request.json())
        await asyncio.sleep(self.delay_s)
        if self.status != 200:
            return web.Response(status=self.status, text="model is loading")
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if self.fail_after_first:
            await resp.write(_sse(self.tokens[0]).split(b"\n\n")[0] + b"\n\n")
            assert request.transport is not None
            request.transport.close()       # the node dies mid-reply
            return resp
        await resp.write(_sse(*self.tokens))
        return resp

    async def _models(self, request: web.Request) -> web.Response:
        if self.status != 200:
            return web.Response(status=self.status)
        return web.json_response({"data": [{"id": "qwen3-8b"}]})

    async def __aenter__(self) -> FakeLlm:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._handle)
        app.router.add_get("/v1/models", self._models)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = self._runner.addresses[0][1]
        self.url = f"http://127.0.0.1:{port}/v1/chat/completions"
        return self

    async def __aexit__(self, *exc):
        assert self._runner
        await self._runner.cleanup()


def _router(primary_url: str, groq: FakeLlm, clock=None, timeout_s: float = 0.5):
    return LlmRouter(LlmBackend("predator", primary_url, "qwen3-8b"),
                     LlmBackend("groq", groq.url, "groq-model", "gsk-test"),
                     first_token_timeout_s=timeout_s, cooldown_s=30.0,
                     clock=clock or Clock())


async def _turn(tenant, router: LlmRouter, text: str = OPEN_TURN):
    agent = Agent("", "", tenant, ToolRegistry(servers={}, allowed=set()), llm=router)
    events: list[dict] = []
    said: list[str] = []
    await agent.respond(text, events.append, said.append)
    return events, " ".join(said)


def _of(events, type_):
    return [e for e in events if e["type"] == type_]


# ── a turn ───────────────────────────────────────────────────────────────────


def test_predator_answers_when_it_is_up(tenant):
    async def run():
        async with FakeLlm("It is sunny on predator.") as predator, \
                FakeLlm("From Groq.") as groq:
            events, said = await _turn(tenant, _router(predator.url, groq))
            return events, said, predator, groq

    events, said, predator, groq = asyncio.run(run())
    assert said == "It is sunny on predator."
    assert (len(predator.requests), len(groq.requests)) == (1, 0)
    assert predator.requests[0]["model"] == "qwen3-8b"
    assert not _of(events, "llm_fallback")
    (llm,) = _of(events, "llm")
    assert (llm["model"], llm["prompt_tokens"], llm["completion_tokens"]) == ("qwen3-8b", 40, 3)


def test_groq_answers_when_predator_is_unreachable(tenant):
    async def run():
        async with FakeLlm("Groq here, it is sunny.") as groq:
            down = f"http://127.0.0.1:{_closed_port()}/v1/chat/completions"
            return await _turn(tenant, _router(down, groq)), groq

    (events, said), groq = asyncio.run(run())
    assert said == "Groq here, it is sunny."
    # Groq gets its own model and key, not predator's.
    assert groq.requests[0]["model"] == "groq-model"
    (fb,) = _of(events, "llm_fallback")
    assert (fb["from"], fb["to"]) == ("predator", "groq")
    assert fb["reason"].startswith("unreachable")
    assert _of(events, "llm")[0]["model"] == "groq-model"
    assert not _of(events, "error")


@pytest.mark.parametrize("predator_kw, reason", [
    ({"status": 503}, "RuntimeError: LLM HTTP 503"),
    ({"delay_s": 1.0}, "no first token within 0.2 s"),
])
def test_groq_answers_when_predator_errors_or_is_too_slow(tenant, predator_kw, reason):
    async def run():
        async with FakeLlm("From predator.", **predator_kw) as predator, \
                FakeLlm("From Groq.") as groq:
            return await _turn(tenant, _router(predator.url, groq, timeout_s=0.2))

    events, said = asyncio.run(run())
    assert said == "From Groq."
    (fb,) = _of(events, "llm_fallback")
    assert fb["reason"].startswith(reason)


def test_a_reply_already_being_spoken_is_not_restarted_on_groq(tenant):
    # Falling back after the first token would say the start of the reply twice.
    async def run():
        async with FakeLlm("It is sunny today. ", "And warm.", fail_after_first=True) \
                as predator, FakeLlm("From Groq.") as groq:
            events, said = await _turn(tenant, _router(predator.url, groq))
            return events, said, groq

    events, said, groq = asyncio.run(run())
    assert groq.requests == []
    assert "From Groq" not in said
    assert not _of(events, "llm_fallback")


def test_after_a_failure_turns_skip_predator_until_the_cooldown_ends(tenant):
    clock = Clock()

    async def run():
        async with FakeLlm("From predator.", status=503) as predator, \
                FakeLlm("From Groq.") as groq:
            router = _router(predator.url, groq, clock=clock)
            await _turn(tenant, router)                 # predator fails: cooldown
            events, _ = await _turn(tenant, router)     # straight to Groq
            assert len(predator.requests) == 1 and not _of(events, "llm_fallback")
            clock.now += 31                             # cooldown over: tried again
            predator.status = 200
            _, said = await _turn(tenant, router)
            assert len(predator.requests) == 2
            return said

    assert asyncio.run(run()) == "From predator."


def test_with_groq_down_too_the_turn_fails_as_before(tenant):
    async def run():
        down = f"http://127.0.0.1:{_closed_port()}/v1/chat/completions"
        router = LlmRouter(LlmBackend("predator", down, "m"),
                           LlmBackend("groq", down, "g", "k"), first_token_timeout_s=0.5)
        return await _turn(tenant, router)

    events, said = asyncio.run(run())
    assert said == ""
    assert len(_of(events, "llm_fallback")) == 1
    (err,) = _of(events, "error")
    assert err["component"] == "turn"


# ── the router ───────────────────────────────────────────────────────────────


def test_without_a_fallback_the_primary_is_always_tried():
    clock = Clock()
    router = LlmRouter(LlmBackend("groq", GROQ_URL, "m", "k"), clock=clock)
    router.failed(router.primary)
    assert router.order() == [router.primary]


def test_success_ends_the_cooldown_early():
    router = LlmRouter(LlmBackend("p", "http://p", "m"), LlmBackend("g", GROQ_URL, "g"),
                       clock=Clock())
    router.failed(router.primary)
    assert router.order() == [router.fallback]
    router.succeeded(router.primary)
    assert router.order() == [router.primary, router.fallback]


# ── the health check ─────────────────────────────────────────────────────────


def test_the_health_check_url_comes_from_the_chat_url():
    b = LlmBackend("p", "http://llm.aicc-llm.svc.cluster.local:8000/v1/chat/completions", "m")
    assert b.models_url == "http://llm.aicc-llm.svc.cluster.local:8000/v1/models"


def test_a_node_that_is_off_is_skipped_without_a_turn_waiting_for_it(tenant):
    async def run():
        async with FakeLlm("From Groq.") as groq:
            down = f"http://127.0.0.1:{_closed_port()}/v1/chat/completions"
            router = _router(down, groq)
            async with aiohttp.ClientSession() as session:
                assert await router.check(session) is False
            assert router.order() == [router.fallback]
            return await _turn(tenant, router)

    events, said = asyncio.run(run())
    assert said == "From Groq."
    assert not _of(events, "llm_fallback")      # never tried: no timeout paid


def test_a_node_that_comes_back_is_used_again_after_its_next_check(tenant):
    async def run():
        async with FakeLlm("From predator.", status=503) as predator, \
                FakeLlm("From Groq.") as groq:
            router = _router(predator.url, groq)
            async with aiohttp.ClientSession() as session:
                assert await router.check(session) is False     # loading the model
                predator.status = 200
                assert await router.check(session) is True
            return await _turn(tenant, router)

    _, said = asyncio.run(run())
    assert said == "From predator."


def test_a_passing_health_check_does_not_end_a_cooldown():
    # /v1/models answering does not prove chat works: the turn that failed decides.
    router = LlmRouter(LlmBackend("p", "http://p/v1/chat/completions", "m"),
                       LlmBackend("g", GROQ_URL, "g"), clock=Clock())
    router.failed(router.primary)
    router.healthy = True
    assert router.order() == [router.fallback]


# ── configuration ────────────────────────────────────────────────────────────


LLM_ENV = ("LLM_URL", "LLM_MODEL", "GROQ_MODEL", "LLM_FALLBACK",
           "LLM_FIRST_TOKEN_TIMEOUT_S", "LLM_COOLDOWN_S")


@pytest.fixture
def env(monkeypatch):
    for name in LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_without_llm_url_groq_is_the_only_llm(env):
    router = llm_router(parse_args([]), groq_key="gsk")
    assert router.primary == LlmBackend("groq", GROQ_URL, GROQ_MODEL, "gsk")
    assert router.fallback is None


def test_llm_url_comes_first_with_groq_behind_it(env):
    env.setenv("LLM_URL", "http://llm.aicc-data.svc.cluster.local:8000/v1/chat/completions")
    env.setenv("LLM_MODEL", "qwen3-8b")
    env.setenv("LLM_FIRST_TOKEN_TIMEOUT_S", "2.5")
    router = llm_router(parse_args([]), groq_key="gsk")
    assert router.primary == LlmBackend(
        "llm.aicc-data.svc.cluster.local",
        "http://llm.aicc-data.svc.cluster.local:8000/v1/chat/completions", "qwen3-8b", "")
    assert router.fallback == LlmBackend("groq", GROQ_URL, GROQ_MODEL, "gsk")
    assert (router.first_token_timeout_s, router.cooldown_s) == (2.5, 30.0)


@pytest.mark.parametrize("fallback, key", [("off", "gsk"), ("groq", "")])
def test_no_fallback_when_turned_off_or_without_a_groq_key(env, fallback, key):
    env.setenv("LLM_URL", "http://predator:8000/v1/chat/completions")
    env.setenv("LLM_FALLBACK", fallback)
    assert llm_router(parse_args([]), groq_key=key).fallback is None


@pytest.mark.parametrize("name, value", [
    ("LLM_URL", "predator:8000"), ("LLM_FIRST_TOKEN_TIMEOUT_S", "0"),
    ("LLM_FALLBACK", "openai"),
])
def test_bad_llm_settings_stop_startup(env, name, value):
    env.setenv(name, value)
    with pytest.raises(SystemExit):
        parse_args([])
