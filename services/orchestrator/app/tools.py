"""MCP tool invocation, with the constraints a live call imposes.

Two rules that differ from normal MCP usage:

  * **The flow calls the tool, not the model.** MCP is designed for model-driven
    tool calling — the LLM emits a call, you execute, it generates an answer. That
    is an extra LLM round trip, roughly 600 ms of dead air. Here the flow engine
    invokes the tool the moment its slots are satisfied, so MCP is a capability
    registry plus RPC rather than an agentic loop.
  * **Hard timeout.** An unbounded tool call on a live call is silence the caller
    cannot interpret. Past the budget we return a `timeout` outcome and let the
    flow decide (usually: apologise and escalate).

Tool output is untrusted input. It lands in the model's context, so a compromised
tenant backend could try to steer the reply. Callers should treat it as data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger("tools")

DEFAULT_TIMEOUT_S = 3.0


def parse_mcp_servers(spec: str) -> dict[str, str]:
    """MCP_SERVERS -> {tool: url}. The spec is "tool=url,tool=url", e.g.
    "lookup_shipment=http://mcp-acme:9101/mcp,check_line_status=http://mcp-swiftnet:9102/mcp".

    Spaces around entries and empty entries (a trailing comma) are ignored. Any
    other mistake is an error at startup: a tool silently left without an
    endpoint would only surface mid-call, as a `no_endpoint` outcome.
    Errors name the entry by position, not by content: a URL may carry credentials.
    """
    servers: dict[str, str] = {}
    for n, entry in enumerate(spec.split(","), 1):
        if not entry.strip():
            continue
        tool, sep, url = (part.strip() for part in entry.partition("="))
        if not (sep and tool and url):
            raise ValueError(f"MCP_SERVERS entry {n} is not tool=url")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"MCP_SERVERS entry {n} ({tool}) is not an http(s) URL")
        if servers.get(tool, url) != url:
            raise ValueError(f"MCP_SERVERS maps {tool} to two different URLs")
        servers[tool] = url
    return servers


def _payload(res) -> dict[str, Any]:
    """Structured result across mcp 1.x/2.x field names."""
    sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if sc:
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    for block in getattr(res, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_raw": text}
    return {}


@dataclass
class ToolCall:
    """A tool's result plus what the event log needs to know about the call."""
    result: dict[str, Any]
    latency_ms: int
    timed_out: bool = False


class ToolRegistry:
    """Per-tenant MCP endpoints and the allowlist that guards them."""

    def __init__(self, servers: dict[str, str], allowed: set[str]):
        # servers: tool_name -> mcp url. Resolved from tenant config server-side;
        # never from anything the model or the browser can influence.
        self.servers = servers
        self.allowed = allowed

    async def call(self, tool: str, args: dict, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
        return (await self.invoke(tool, args, timeout_s)).result

    async def invoke(self, tool: str, args: dict,
                     timeout_s: float = DEFAULT_TIMEOUT_S) -> ToolCall:
        if tool not in self.allowed:
            # A tool absent from the tenant's allowlist must fail closed, loudly.
            log.error("tool %r not in tenant allowlist", tool)
            return ToolCall({"outcome": "error", "reason": "not_permitted"}, latency_ms=0)

        url = self.servers.get(tool)
        if not url:
            log.error("no MCP endpoint configured for %r", tool)
            return ToolCall({"outcome": "error", "reason": "no_endpoint"}, latency_ms=0)

        t0 = time.perf_counter()

        def elapsed_ms() -> int:
            return int((time.perf_counter() - t0) * 1000)

        try:
            result = await asyncio.wait_for(self._invoke(url, tool, args), timeout=timeout_s)
            call = ToolCall(result, elapsed_ms())
        except TimeoutError:
            log.warning("tool %s timed out after %.1fs", tool, timeout_s)
            call = ToolCall({"outcome": "timeout"}, elapsed_ms(), timed_out=True)
        except Exception as exc:
            log.exception("tool %s failed", tool)
            call = ToolCall({"outcome": "error", "reason": type(exc).__name__}, elapsed_ms())
        log.info("tool %s took %d ms", tool, call.latency_ms)
        return call

    @staticmethod
    async def _invoke(url: str, tool: str, args: dict) -> dict:
        # Connect per call: measured ~30 ms setup, ~3 ms for the call itself. Well
        # inside budget, and it avoids holding sessions open across a long call.
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return _payload(await session.call_tool(tool, args))
