"""The STT stream is opened in the tenant's language, not the en-US default.

STT and the orchestrator start together. The locale arrives with the
orchestrator's Ready, and the STT language is fixed by the stream's first
message, so that message must wait for it. Sent early, a French tenant was
transcribed with English start tokens and the caller came out translated.
"""

from __future__ import annotations

import asyncio

import server
from gen import orchestrator_pb2 as ob


class FakePeer:
    async def close(self):
        pass


def _call() -> server.Call:
    return server.Call(pc=FakePeer(), cfg={"session_id": "mv-1"})


def _ready(locale: str) -> ob.Directive:
    return ob.Directive(ready=ob.Ready(tenant_id="maison-verte", display_name="Maison Verte",
                                       locale=locale, voice="Sofia"))


async def _first_config(call: server.Call, before_first):
    """Start the request stream, run `before_first`, return the first request."""
    reqs = call._stt_requests()
    first = asyncio.ensure_future(anext(reqs))
    await asyncio.sleep(0.05)
    assert not first.done(), "STT config went out before the tenant was known"
    await before_first()
    req = await asyncio.wait_for(first, 1)
    await reqs.aclose()
    return req


def test_stt_config_waits_for_the_tenant_locale():
    async def scenario():
        call = _call()
        return await _first_config(call, lambda: call._apply(_ready("fr-FR"), None, ""))

    req = asyncio.run(scenario())
    assert req.config.language == "fr"


def test_an_english_tenant_still_gets_english():
    async def scenario():
        call = _call()
        return await _first_config(call, lambda: call._apply(_ready("en-US"), None, ""))

    assert asyncio.run(scenario()).config.language == "en"


def test_without_a_tenant_stt_falls_back_to_the_default_after_the_wait():
    async def scenario():
        call = _call()
        call.TENANT_WAIT_S = 0.05
        reqs = call._stt_requests()
        req = await asyncio.wait_for(anext(reqs), 1)
        await reqs.aclose()
        return req

    assert asyncio.run(scenario()).config.language == "en"


def test_a_call_ending_before_the_tenant_closes_the_stt_stream():
    async def scenario():
        call = _call()
        reqs = call._stt_requests()
        nxt = asyncio.ensure_future(anext(reqs))
        await asyncio.sleep(0.05)
        await call.end()
        try:
            await asyncio.wait_for(nxt, 1)
        except StopAsyncIteration:
            return True
        return False

    assert asyncio.run(scenario())
