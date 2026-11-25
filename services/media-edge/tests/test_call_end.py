"""The orchestrator side of a call: retried while it has not started, and when it
is over, the whole call is over.

Runs Call.run_orchestrator against an in-process grpc.aio server standing in for
the orchestrator. No WebRTC: the peer connection is a fake that records close().
"""

from __future__ import annotations

import asyncio
import contextlib

import grpc

import server
from gen import orchestrator_pb2 as ob
from gen import orchestrator_pb2_grpc as ob_grpc


class FakePeer:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class Orchestrator(ob_grpc.OrchestratorServicer):
    """Refuses the first `refuse` Session starts with UNAVAILABLE (a drain), then
    answers Ready + one Say and hangs up, like an escalation."""

    def __init__(self, refuse: int):
        self.refuse = refuse
        self.starts = 0

    async def Session(self, request_iterator, context):  # noqa: N802
        first = await anext(aiter(request_iterator))
        assert first.WhichOneof("event") == "start"
        self.starts += 1
        if self.starts <= self.refuse:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "draining")
        yield ob.Directive(ready=ob.Ready(tenant_id="acme-delivery", display_name="Acme",
                                          locale="en-US", voice="Jason"))
        yield ob.Directive(hangup=ob.Hangup(reason="escalate"))


@contextlib.asynccontextmanager
async def orchestrator(servicer):
    srv = grpc.aio.server()
    ob_grpc.add_OrchestratorServicer_to_server(servicer, srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    await srv.start()
    try:
        yield f"127.0.0.1:{port}"
    finally:
        await srv.stop(None)


def _call() -> server.Call:
    call = server.Call(pc=FakePeer(), cfg={"session_id": "acme-1"})
    call.START_RETRIES = (0.01, 0.01)
    return call


def _sent_types(call) -> list[str]:
    import json
    return [json.loads(m)["type"] for m in call.pending if isinstance(m, str)]


def test_a_start_refused_during_a_drain_is_retried_and_the_call_goes_ahead():
    async def scenario():
        servicer = Orchestrator(refuse=1)
        async with orchestrator(servicer) as target:
            call = _call()
            await call.run_orchestrator(target, "acme", "http://127.0.0.1:9")
            return servicer, call

    servicer, call = asyncio.run(scenario())
    assert servicer.starts == 2
    assert "tenant" in _sent_types(call)
    assert "error" not in _sent_types(call)


def test_when_the_orchestrator_side_ends_the_call_is_closed():
    async def scenario():
        async with orchestrator(Orchestrator(refuse=0)) as target:
            call = _call()
            await call.run_orchestrator(target, "acme", "http://127.0.0.1:9")
            return call

    call = asyncio.run(scenario())
    assert call.closed and call.pc.closed               # media and STT stop too
    assert call.audio_q.get_nowait() is None            # the STT stream is told to end
    assert _sent_types(call)[-1] == "closed"


def test_refused_every_time_reports_the_error_and_still_ends_the_call():
    async def scenario():
        servicer = Orchestrator(refuse=99)
        async with orchestrator(servicer) as target:
            call = _call()
            await call.run_orchestrator(target, "acme", "http://127.0.0.1:9")
            return servicer, call

    servicer, call = asyncio.run(scenario())
    assert servicer.starts == 1 + len(server.Call.START_RETRIES)
    assert "error" in _sent_types(call)
    assert call.pc.closed
