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
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger("tools")

DEFAULT_TIMEOUT_S = 3.0


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


class ToolRegistry:
    """Per-tenant MCP endpoints and the allowlist that guards them."""

    def __init__(self, servers: dict[str, str], allowed: set[str]):
        # servers: tool_name -> mcp url. Resolved from tenant config server-side;
        # never from anything the model or the browser can influence.
        self.servers = servers
        self.allowed = allowed

    async def call(self, tool: str, args: dict, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
        if tool not in self.allowed:
            # A tool absent from the tenant's allowlist must fail closed, loudly.
            log.error("tool %r not in tenant allowlist", tool)
            return {"outcome": "error", "reason": "not_permitted"}

        url = self.servers.get(tool)
        if not url:
            log.error("no MCP endpoint configured for %r", tool)
            return {"outcome": "error", "reason": "no_endpoint"}

        t0 = time.perf_counter()
        try:
            return await asyncio.wait_for(self._invoke(url, tool, args), timeout=timeout_s)
        except TimeoutError:
            log.warning("tool %s timed out after %.1fs", tool, timeout_s)
            return {"outcome": "timeout"}
        except Exception as exc:
            log.exception("tool %s failed", tool)
            return {"outcome": "error", "reason": type(exc).__name__}
        finally:
            log.info("tool %s took %.0f ms", tool, (time.perf_counter() - t0) * 1000)

    @staticmethod
    async def _invoke(url: str, tool: str, args: dict) -> dict:
        # Connect per call: measured ~30 ms setup, ~3 ms for the call itself. Well
        # inside budget, and it avoids holding sessions open across a long call.
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return _payload(await session.call_tool(tool, args))
