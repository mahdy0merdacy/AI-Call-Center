"""GET /tenants: the client picker's list, relayed from the orchestrator.

Runs the real aiohttp app against an in-process grpc.aio server standing in for
the orchestrator, so the checks cover the channel, the deadline and the JSON
shape together.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time

import grpc
import pytest
from aiohttp.test_utils import TestClient, TestServer

import server
from gen import orchestrator_pb2 as ob
from gen import orchestrator_pb2_grpc as ob_grpc

TENANTS = [
    ob.TenantSummary(slug="acme", display_name="Acme Delivery", locale="en-US",
                     source="test"),
    ob.TenantSummary(slug="maison-verte-k3x9q2", display_name="Maison Verte",
                     locale="en-US", source="platform", documents=3),
]


class FakeOrchestrator(ob_grpc.OrchestratorServicer):
    """Implements ListTenants only: the picker never opens a Session."""

    def __init__(self, tenants=(), *, delay_s: float = 0.0,
                 fail_with: grpc.StatusCode | None = None):
        self.tenants = list(tenants)
        self.delay_s = delay_s
        self.fail_with = fail_with

    async def ListTenants(self, request, context):  # noqa: N802
        await asyncio.sleep(self.delay_s)
        if self.fail_with is not None:
            await context.abort(self.fail_with, "fake failure")
        return ob.ListTenantsResponse(tenants=self.tenants)


@contextlib.asynccontextmanager
async def orchestrator(servicer: FakeOrchestrator, port: int = 0):
    srv = grpc.aio.server()
    ob_grpc.add_OrchestratorServicer_to_server(servicer, srv)
    bound = srv.add_insecure_port(f"127.0.0.1:{port}")
    await srv.start()
    try:
        yield f"127.0.0.1:{bound}"
    finally:
        await srv.stop(None)


@contextlib.asynccontextmanager
async def edge(orch_target: str):
    app = server.make_app(server.parse_args(["--orchestrator", orch_target]))
    async with TestClient(TestServer(app)) as client:
        yield client


async def _get(client: TestClient, path: str = "/tenants") -> tuple[int, dict]:
    resp = await client.get(path)
    return resp.status, await resp.json()


def _free_port() -> int:
    """A port nothing listens on: bound, then released."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_lists_what_the_orchestrator_returns_in_its_order():
    async def run():
        async with orchestrator(FakeOrchestrator(TENANTS)) as target, edge(target) as client:
            resp = await client.get("/tenants")
            return resp.status, resp.headers.get("Cache-Control"), await resp.json()

    status, cache, body = asyncio.run(run())
    assert status == 200
    assert cache == "no-store"          # a client who just signed up shows on reload
    assert body == {"tenants": [
        {"slug": "acme", "display_name": "Acme Delivery", "locale": "en-US",
         "source": "test", "documents": 0},
        {"slug": "maison-verte-k3x9q2", "display_name": "Maison Verte", "locale": "en-US",
         "source": "platform", "documents": 3},
    ]}


def test_an_empty_directory_is_an_empty_list_not_an_error():
    async def run():
        async with orchestrator(FakeOrchestrator()) as target, edge(target) as client:
            return await _get(client)

    assert asyncio.run(run()) == (200, {"tenants": []})


def test_503_when_the_orchestrator_is_down():
    async def run():
        async with edge(f"127.0.0.1:{_free_port()}") as client:
            t0 = time.monotonic()
            status, body = await _get(client)
            return status, body, time.monotonic() - t0

    status, body, took = asyncio.run(run())
    assert status == 503
    assert body == {"error": "orchestrator ListTenants failed: UNAVAILABLE"}
    assert took < server.LIST_TENANTS_TIMEOUT_S     # refused at once, not waited out


@pytest.mark.parametrize("code", [
    grpc.StatusCode.UNIMPLEMENTED,      # an orchestrator image older than ListTenants
    grpc.StatusCode.INTERNAL,
])
def test_503_when_the_orchestrator_fails(code):
    async def run():
        async with orchestrator(FakeOrchestrator(fail_with=code)) as target, \
                edge(target) as client:
            return await _get(client)

    assert asyncio.run(run()) == (503, {"error": f"orchestrator ListTenants failed: {code.name}"})


def test_a_hung_orchestrator_hits_the_deadline_without_stalling_the_edge(monkeypatch):
    monkeypatch.setattr(server, "LIST_TENANTS_TIMEOUT_S", 1.0)

    async def run():
        async with orchestrator(FakeOrchestrator(TENANTS, delay_s=30)) as target, \
                edge(target) as client:
            t0 = time.monotonic()
            pending = asyncio.ensure_future(_get(client))
            await asyncio.sleep(0.1)            # /tenants is now waiting on the orchestrator
            health = await client.get("/healthz")
            health_s = time.monotonic() - t0
            status, body = await pending
            return health.status, health_s, status, body, time.monotonic() - t0

    health, health_s, status, body, took = asyncio.run(run())
    assert health == 200 and health_s < 0.6     # answered while /tenants was still waiting
    assert status == 503
    assert body == {"error": "orchestrator ListTenants failed: DEADLINE_EXCEEDED"}
    assert 1.0 <= took < 3.0


def test_a_restarted_orchestrator_is_picked_up_on_the_next_request():
    async def run():
        port = _free_port()
        statuses = []
        async with edge(f"127.0.0.1:{port}") as client:
            async with orchestrator(FakeOrchestrator(TENANTS), port=port):
                statuses.append((await _get(client))[0])
            statuses.append((await _get(client))[0])            # down
            async with orchestrator(FakeOrchestrator(TENANTS), port=port):
                statuses.append((await _get(client))[0])        # back: no stale backoff
        return statuses

    assert asyncio.run(run()) == [200, 503, 200]


@pytest.mark.parametrize("path", ["/", "/w/acme"])
def test_the_test_page_carries_the_client_picker(path):
    async def run():
        async with edge(f"127.0.0.1:{_free_port()}") as client:
            resp = await client.get(path)
            return resp.status, await resp.text()

    status, html = asyncio.run(run())
    assert status == 200
    assert '<select id="client"' in html and "fetch('/tenants'" in html
