"""The servicer around the agent: configuration, the tenant picker, call starts for
platform clients, and the SIGTERM drain.

The drain tests run a real gRPC server on a free localhost port, because what
they check (health, refusals, a call surviving the shutdown) is what the edge
and Kubernetes see over the wire.
"""

from __future__ import annotations

import asyncio
import logging

import grpc
import pytest
from aicc_events import EventPublisher, ev
from grpc_health.v1 import health_pb2, health_pb2_grpc

import app.server as server_module
from app.agent import GROQ_URL
from app.server import (
    SERVICE_NAME,
    OrchestratorServicer,
    Session,
    parse_args,
    shutdown,
    start_server,
)
from app.tenants import PlatformTenant, TenantDirectory
from gen import orchestrator_pb2 as pb
from gen import orchestrator_pb2_grpc as pb_grpc

BISTRO = PlatformTenant(id="6f1c9a52-8d0e-4b7a-9c43-2b1e0f5d7a11", name="Bistro Lumière",
                        slug="bistro", is_active=True, documents=3)
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING


def _start(slug: str = "test", call_id: str = "call-1") -> pb.CallEvent:
    return pb.CallEvent(start=pb.StartCall(tenant_slug=slug, call_id=call_id))


HANGUP = pb.CallEvent(end=pb.EndCall(reason="hangup"))


class RecordingPublisher(EventPublisher):
    """Keeps the call events instead of sending them to Kafka."""

    def __init__(self) -> None:
        super().__init__(ev.SERVICE_ORCHESTRATOR, instance="test", build="test")
        self.events: list[ev.Event] = []

    def publish(self, event: ev.Event) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.WhichOneof("payload") or "" for e in self.events]


class FakePlatform:
    def __init__(self, *rows: PlatformTenant, down: bool = False):
        self.rows = {r.slug: r for r in rows}
        self.down = down
        self.fetches: list[str] = []
        self.on_fetch = None            # called during a lookup, before it answers

    async def fetch(self, slug):
        self.fetches.append(slug)
        if self.on_fetch:
            self.on_fetch()
        if self.down:
            raise ConnectionError("connection refused")
        return self.rows.get(slug)

    async def fetch_active(self):
        if self.down:
            raise ConnectionError("connection refused")
        return list(self.rows.values())


def _servicer(tenant, platform: FakePlatform | None = None) -> OrchestratorServicer:
    return OrchestratorServicer(TenantDirectory({"test": tenant}, platform), groq_key="",
                                model="test-model", mcp_endpoints={},
                                publisher=RecordingPublisher())


class FakeContext:
    """Just enough of grpc's ServicerContext: abort records the status and raises."""

    def __init__(self) -> None:
        self.code: grpc.StatusCode | None = None
        self.details = ""

    async def abort(self, code, details=""):
        self.code, self.details = code, details
        raise grpc.aio.AbortError(details)


def _call(servicer: OrchestratorServicer, *messages: pb.CallEvent):
    """One call through Session(); returns (directives, context)."""
    context = FakeContext()

    async def run():
        async def requests():
            for m in messages:
                yield m
                await asyncio.sleep(0.05)
        return [d async for d in servicer.Session(requests(), context)]

    return asyncio.run(run()), context


# ── configuration ────────────────────────────────────────────────────────────

ENV = ("HOST", "PORT", "LLM_MODEL", "LLM_URL", "MCP_SERVERS", "KNOWLEDGE_URL",
       "SHUTDOWN_GRACE_S")


@pytest.fixture
def env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_are_the_local_setup(env):
    args = parse_args([])
    assert (args.host, args.port, args.llm_model, args.llm_url) == \
        ("0.0.0.0", 50052, "qwen/qwen3.8-27b", GROQ_URL)
    assert (args.knowledge_url, args.shutdown_grace) == ("", 25.0)
    assert args.mcp_endpoints == {"lookup_shipment": "http://127.0.0.1:9101/mcp",
                                  "reschedule_delivery": "http://127.0.0.1:9101/mcp",
                                  "check_line_status": "http://127.0.0.1:9102/mcp"}


def test_every_setting_comes_from_the_environment(env):
    env.setenv("HOST", "127.0.0.1")
    env.setenv("PORT", "6000")
    env.setenv("LLM_MODEL", "sim-llm")
    env.setenv("LLM_URL", "http://fake-llm:8099/v1/chat/completions")
    env.setenv("KNOWLEDGE_URL", "http://knowledge:8100")
    env.setenv("SHUTDOWN_GRACE_S", "12.5")
    env.setenv("MCP_SERVERS", "lookup_shipment=http://mcp-acme:9101/mcp")
    args = parse_args(["--mcp-acme", "http://ignored:1/mcp"])   # MCP_SERVERS wins
    assert (args.host, args.port, args.llm_model) == ("127.0.0.1", 6000, "sim-llm")
    assert args.llm_url == "http://fake-llm:8099/v1/chat/completions"
    assert (args.knowledge_url, args.shutdown_grace) == ("http://knowledge:8100", 12.5)
    assert args.mcp_endpoints == {"lookup_shipment": "http://mcp-acme:9101/mcp"}


def test_a_flag_still_overrides_its_variable(env):
    env.setenv("PORT", "6000")
    assert parse_args(["--port", "7000"]).port == 7000


def test_an_empty_variable_means_the_default(env):
    # Compose writes `PORT: ""` for a variable left unset in .env.
    for name in ENV:
        env.setenv(name, "")
    args = parse_args([])
    assert (args.port, args.shutdown_grace, args.knowledge_url) == (50052, 25.0, "")
    assert "lookup_shipment" in args.mcp_endpoints


@pytest.mark.parametrize("name, value", [
    ("MCP_SERVERS", "lookup_shipment"),
    ("KNOWLEDGE_URL", "knowledge:8100"),
    ("SHUTDOWN_GRACE_S", "-1"),
    ("PORT", "fifty"),
])
def test_a_bad_setting_stops_startup(env, name, value, capsys):
    env.setenv(name, value)
    with pytest.raises(SystemExit):
        parse_args([])
    assert "error" in capsys.readouterr().err


# ── the tenant picker ────────────────────────────────────────────────────────


def test_list_tenants_returns_test_tenants_then_platform_clients(tenant):
    servicer = _servicer(tenant, FakePlatform(BISTRO))
    resp = asyncio.run(servicer.ListTenants(pb.ListTenantsRequest(), None))
    assert [(t.slug, t.display_name, t.locale, t.source, t.documents)
            for t in resp.tenants] == [("test", "Test Co", "en-US", "test", 0),
                                       ("bistro", "Bistro Lumière", "en-US", "platform", 3)]


def test_list_tenants_with_postgres_down_still_lists_test_tenants(tenant):
    servicer = _servicer(tenant, FakePlatform(BISTRO, down=True))
    resp = asyncio.run(servicer.ListTenants(pb.ListTenantsRequest(), None))
    assert [t.slug for t in resp.tenants] == ["test"]


# ── call starts ──────────────────────────────────────────────────────────────


def test_a_platform_client_is_callable_with_its_defaults(tenant):
    servicer = _servicer(tenant, FakePlatform(BISTRO))
    directives, context = _call(servicer, _start("bistro"), HANGUP)
    assert context.code is None
    ready = directives[0].ready
    assert (ready.tenant_id, ready.display_name, ready.locale, ready.voice) == \
        (BISTRO.id, "Bistro Lumière", "en-US", "Aria")
    assert (list(ready.flows), list(ready.tools)) == ([], [])
    said = " ".join(d.say.text for d in directives if d.WhichOneof("directive") == "say")
    assert said == "Hello, you've reached Bistro Lumière. How can I help you today?"
    started = servicer.publisher.events[0]
    assert started.tenant_id == BISTRO.id and started.call_started.tenant_slug == "bistro"


def test_an_unknown_slug_is_not_found(tenant):
    servicer = _servicer(tenant, FakePlatform(BISTRO))
    _, context = _call(servicer, _start("nobody"))
    assert context.code == grpc.StatusCode.NOT_FOUND


def test_postgres_down_refuses_an_unseen_client_as_unavailable_not_unknown(tenant):
    servicer = _servicer(tenant, FakePlatform(BISTRO, down=True))
    _, context = _call(servicer, _start("bistro"))
    assert context.code == grpc.StatusCode.UNAVAILABLE
    _, context = _call(servicer, _start("test"))   # test tenants never need Postgres
    assert context.code is None


def test_retrieval_is_logged_per_turn_but_not_published(tenant, caplog):
    publisher = RecordingPublisher()
    session = Session(tenant, agent=None, call_id="c1",
                      events=publisher.for_call("c1", tenant.tenant_id))
    with caplog.at_level(logging.INFO, logger="orch"):
        session.on_event({"type": "retrieval", "turn": 2, "ok": True, "hits": 4,
                          "latency_ms": 85, "took_ms": 60, "error": ""})
        session.on_event({"type": "retrieval", "turn": 3, "ok": False, "hits": 0,
                          "latency_ms": 1501, "took_ms": 0, "error": "no answer within 1.5 s"})
    assert "[c1] retrieval: 4 hit(s) in 85 ms (search 60 ms)" in caplog.text
    assert "[c1] retrieval failed after 1501 ms: no answer within 1.5 s" in caplog.text
    assert publisher.events == []


# ── SIGTERM ──────────────────────────────────────────────────────────────────


def test_while_draining_a_new_call_is_refused_as_unavailable(tenant):
    platform = FakePlatform(BISTRO)
    servicer = _servicer(tenant, platform)
    servicer.draining = True
    for slug in ("test", "bistro", "nobody"):       # even an unknown one: retry elsewhere
        directives, context = _call(servicer, _start(slug))
        assert context.code == grpc.StatusCode.UNAVAILABLE, slug
        assert directives == [] and servicer.active == {}
    assert servicer.publisher.events == []
    assert platform.fetches == []                  # refused before any lookup


def test_a_drain_that_begins_during_the_lookup_still_refuses_the_call(tenant):
    platform = FakePlatform(BISTRO)
    servicer = _servicer(tenant, platform)

    def sigterm():
        servicer.draining = True

    platform.on_fetch = sigterm
    directives, context = _call(servicer, _start("bistro"))
    assert platform.fetches == ["bistro"]
    assert context.code == grpc.StatusCode.UNAVAILABLE
    assert directives == [] and servicer.active == {}


def test_drain_returns_as_soon_as_the_last_call_ends(tenant):
    servicer = _servicer(tenant)

    async def scenario():
        servicer.active["c1"] = object()
        asyncio.get_running_loop().call_later(0.2, servicer.active.clear)
        return await servicer.drain(grace_s=5)

    assert asyncio.run(scenario()) == 0


def test_drain_gives_up_after_the_grace_period_or_a_second_signal(tenant):
    servicer = _servicer(tenant)
    servicer.active["c1"] = object()
    assert asyncio.run(servicer.drain(grace_s=0.1)) == 1

    async def interrupted():
        second = asyncio.Event()
        asyncio.get_running_loop().call_later(0.1, second.set)
        return await asyncio.wait_for(servicer.drain(grace_s=30, interrupted=second), 2)

    assert asyncio.run(interrupted()) == 1


class Caller:
    """The edge's end of one call, over a real channel."""

    def __init__(self, stub: pb_grpc.OrchestratorStub, start: pb.CallEvent):
        self.up: asyncio.Queue[pb.CallEvent | None] = asyncio.Queue()
        self.up.put_nowait(start)
        self.call = stub.Session(self._events())

    async def _events(self):
        while (msg := await self.up.get()) is not None:
            yield msg

    async def says(self) -> str:
        """Directives until the next Say, and its text."""
        while True:
            d = await self.call.read()
            assert d is not grpc.aio.EOF, "the call ended"
            if d.WhichOneof("directive") == "say":
                return d.say.text

    async def ends(self) -> None:
        """Read what is left until the call ends; raises if it ended in error."""
        while await self.call.read() is not grpc.aio.EOF:
            pass

    def hang_up(self) -> None:
        self.up.put_nowait(HANGUP)
        self.up.put_nowait(None)


def _over_the_wire(tenant, scenario):
    """Run scenario(servicer, server, health, stub, health_stub) against a real server."""
    async def run():
        servicer = _servicer(tenant)
        server, health, port = await start_server(servicer, "127.0.0.1", 0)
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                await scenario(servicer, server, health, pb_grpc.OrchestratorStub(channel),
                               health_pb2_grpc.HealthStub(channel))
        finally:
            await server.stop(None)
        return servicer

    return asyncio.run(run())


def test_sigterm_stops_routing_refuses_new_calls_and_lets_live_ones_finish(tenant):
    async def scenario(servicer, server, health, stub, health_stub):
        live = Caller(stub, _start(call_id="live"))
        assert await live.says() == "Test Co, how can I help?"

        stopping = asyncio.ensure_future(shutdown(server, health, servicer, grace_s=10))
        await asyncio.sleep(0.2)
        for service in ("", SERVICE_NAME):          # Kubernetes probes either name
            check = await health_stub.Check(health_pb2.HealthCheckRequest(service=service))
            assert check.status == NOT_SERVING

        late = Caller(stub, _start(call_id="late"))
        with pytest.raises(grpc.aio.AioRpcError) as refused:
            await late.call.read()
        assert refused.value.code() == grpc.StatusCode.UNAVAILABLE
        late.up.put_nowait(None)

        # The picker still answers, and the live call carries on as normal.
        assert (await stub.ListTenants(pb.ListTenantsRequest())).tenants[0].slug == "test"
        live.up.put_nowait(pb.CallEvent(
            transcript=pb.Transcript(text="where is my parcel", is_final=True)))
        assert await live.says() == "Can I take your shipment ID?"
        assert not stopping.done()

        live.hang_up()
        await live.ends()
        await asyncio.wait_for(stopping, 3)          # drained: the server stops at once

    servicer = _over_the_wire(tenant, scenario)
    ended = [e for e in servicer.publisher.events
             if e.WhichOneof("payload") == "call_ended"]
    assert [(e.call_id, e.call_ended.reason) for e in ended] == \
        [("live", ev.END_REASON_CALLER_HUNG_UP)]


def test_a_call_still_open_when_the_grace_runs_out_is_cut_as_an_error(tenant, monkeypatch):
    monkeypatch.setattr(server_module, "STOP_GRACE_S", 0.1)

    async def scenario(servicer, server, health, stub, health_stub):
        stuck = Caller(stub, _start(call_id="stuck"))
        assert await stuck.says() == "Test Co, how can I help?"
        await asyncio.wait_for(shutdown(server, health, servicer, grace_s=0.2), 3)
        with pytest.raises(grpc.aio.AioRpcError):
            await stuck.ends()
        stuck.up.put_nowait(None)

    servicer = _over_the_wire(tenant, scenario)
    kinds = servicer.publisher.kinds()
    assert kinds[-1] == "call_ended"
    assert servicer.publisher.events[-1].call_ended.reason == ev.END_REASON_ERROR
    assert servicer.active == {}


def test_the_container_health_check_follows_the_drain(tenant, monkeypatch):
    from app import healthcheck

    async def run():
        server, health, port = await start_server(_servicer(tenant), "127.0.0.1", 0)
        monkeypatch.setenv("PORT", str(port))
        try:
            # In a thread: the check is a blocking client, and this loop is the server.
            serving = await asyncio.to_thread(healthcheck.main)
            await health.enter_graceful_shutdown()
            draining = await asyncio.to_thread(healthcheck.main)
        finally:
            await server.stop(None)
        gone = await asyncio.to_thread(healthcheck.main)
        return serving, draining, gone

    assert asyncio.run(run()) == (0, 1, 1)
