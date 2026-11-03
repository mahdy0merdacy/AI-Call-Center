"""ToolRegistry guards: allowlist fails closed, and a slow tool cannot stall a call."""

from __future__ import annotations

import asyncio

from app.tools import ToolRegistry


def _call(registry: ToolRegistry, tool: str, **kw) -> dict:
    return asyncio.run(registry.call(tool, {"shipment_id": "AC4471X209"}, **kw))


def test_tool_outside_allowlist_is_refused():
    reg = ToolRegistry(servers={"lookup_shipment": "http://x/mcp"}, allowed=set())
    assert _call(reg, "lookup_shipment") == {"outcome": "error", "reason": "not_permitted"}


def test_allowed_tool_without_endpoint_is_refused():
    reg = ToolRegistry(servers={}, allowed={"lookup_shipment"})
    assert _call(reg, "lookup_shipment") == {"outcome": "error", "reason": "no_endpoint"}


def test_slow_tool_times_out(monkeypatch):
    async def slow(url, tool, args):
        await asyncio.sleep(5)

    monkeypatch.setattr(ToolRegistry, "_invoke", staticmethod(slow))
    reg = ToolRegistry(servers={"lookup_shipment": "http://x/mcp"},
                       allowed={"lookup_shipment"})
    assert _call(reg, "lookup_shipment", timeout_s=0.05) == {"outcome": "timeout"}


def test_failing_tool_reports_error_type(monkeypatch):
    async def broken(url, tool, args):
        raise ConnectionError("refused")

    monkeypatch.setattr(ToolRegistry, "_invoke", staticmethod(broken))
    reg = ToolRegistry(servers={"lookup_shipment": "http://x/mcp"},
                       allowed={"lookup_shipment"})
    assert _call(reg, "lookup_shipment") == {"outcome": "error",
                                             "reason": "ConnectionError"}
