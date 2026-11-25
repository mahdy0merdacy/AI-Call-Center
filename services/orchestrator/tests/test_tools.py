"""ToolRegistry guards: allowlist fails closed, and a slow tool cannot stall a call.
Plus MCP_SERVERS, which says where each tool's MCP server is."""

from __future__ import annotations

import asyncio
import re

import pytest

from app.tools import ToolRegistry, parse_mcp_servers


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


# ── MCP_SERVERS ──────────────────────────────────────────────────────────────


def test_mcp_servers_maps_each_tool_to_its_url():
    spec = (" lookup_shipment = http://mcp-acme:9101/mcp ,"
            "reschedule_delivery=http://mcp-acme:9101/mcp,"
            "check_line_status=https://mcp-swiftnet:9102/mcp?x=1,")   # trailing comma
    assert parse_mcp_servers(spec) == {
        "lookup_shipment": "http://mcp-acme:9101/mcp",
        "reschedule_delivery": "http://mcp-acme:9101/mcp",
        "check_line_status": "https://mcp-swiftnet:9102/mcp?x=1",
    }


def test_mcp_servers_empty_maps_nothing():
    assert parse_mcp_servers("") == {} and parse_mcp_servers(" , ") == {}


@pytest.mark.parametrize("spec, error", [
    ("http://user:s3cret@mcp-acme:9101/mcp", "entry 1 is not tool=url"),
    ("lookup_shipment=", "entry 1 is not tool=url"),
    ("a=http://x/mcp,=http://y/mcp", "entry 2 is not tool=url"),
    ("lookup_shipment=mcp-acme:9101/mcp", "entry 1 (lookup_shipment) is not an http"),
    ("lookup_shipment=ftp://s3cret@x/mcp", "is not an http"),
    ("t=http://a/mcp,t=http://b/mcp", "maps t to two different URLs"),
])
def test_mcp_servers_refuses_a_malformed_spec_without_echoing_it(spec, error):
    with pytest.raises(ValueError, match=re.escape(error)) as exc:
        parse_mcp_servers(spec)
    assert "s3cret" not in str(exc.value)
