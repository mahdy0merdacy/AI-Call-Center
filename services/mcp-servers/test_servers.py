"""Exercise both MCP servers the way the orchestrator will.

Connects over streamable-http, lists what each server advertises, then calls every
tool — including the failure branches, because a test that only walks the happy
path proves nothing.

    python acme_delivery.py --port 9101 &
    python swiftnet_isp.py  --port 9102 &
    python test_servers.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

SERVERS = {
    "acme-delivery": "http://127.0.0.1:9101/mcp",
    "swiftnet-isp": "http://127.0.0.1:9102/mcp",
}

CASES = [
    ("acme-delivery", "lookup_shipment", {"shipment_id": "AC4471X209"}, "in_transit"),
    ("acme-delivery", "lookup_shipment", {"shipment_id": "AC8823B110"}, "delivered"),
    ("acme-delivery", "lookup_shipment", {"shipment_id": "AC1199Z044"}, "delayed"),
    ("acme-delivery", "lookup_shipment", {"shipment_id": "ZZ0000Z000"}, "not_found"),
    ("acme-delivery", "lookup_shipment", {"shipment_id": "nope"}, "not_found"),
    ("acme-delivery", "reschedule_delivery",
     {"shipment_id": "AC4471X209", "new_date": "2026-10-05"}, "ok"),
    ("acme-delivery", "reschedule_delivery",
     {"shipment_id": "AC0000A000", "new_date": "2026-10-05"}, "too_late"),
    ("swiftnet-isp", "check_line_status", {"phone_number": "0142887711"}, "line_ok"),
    ("swiftnet-isp", "check_line_status", {"phone_number": "0298445102"}, "area_outage"),
    ("swiftnet-isp", "check_line_status", {"phone_number": "0155093388"}, "account_suspended"),
    # callers read numbers aloud with spaces — the tool must cope
    ("swiftnet-isp", "check_line_status", {"phone_number": "01 42 88 77 11"}, "line_ok"),
    ("swiftnet-isp", "check_line_status", {"phone_number": "0000000000"}, "not_found"),
]


def _result_payload(res) -> dict:
    """Pull the structured dict out of a CallToolResult across SDK shapes."""
    # mcp 2.x renamed these to snake_case (1.x used structuredContent/inputSchema)
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


async def run_server(name: str, url: str, cases: list) -> tuple[int, int]:
    passed = failed = 0
    # mcp 2.x yields (read, write); 1.x yielded a third element.
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            print(f"\n=== {name} — advertises {len(tools)} tool(s) ===")
            for t in tools:
                schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
                params = list(schema.get("properties", {}))
                print(f"  {t.name}({', '.join(params)})")
                print(f"    {(t.description or '').strip()[:95]}")

            print(f"  -- calls --")
            for _, tool, args, expect in cases:
                t0 = time.perf_counter()
                try:
                    res = await session.call_tool(tool, args)
                    payload = _result_payload(res)
                    got = payload.get("outcome", "<no outcome field>")
                except Exception as exc:
                    got = f"EXCEPTION {type(exc).__name__}: {exc}"
                    payload = {}
                ms = (time.perf_counter() - t0) * 1000
                ok = got == expect
                passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
                arg = next(iter(args.values()))
                print(f"    {'ok ' if ok else 'FAIL'} {tool}({arg!r:<18}) -> "
                      f"{got:<18} {ms:6.1f} ms" + ("" if ok else f"   expected {expect}"))
    return passed, failed


async def main() -> int:
    total_p = total_f = 0
    for name, url in SERVERS.items():
        cases = [c for c in CASES if c[0] == name]
        try:
            p, f = await run_server(name, url, cases)
        except Exception as exc:
            print(f"\n=== {name} — UNREACHABLE at {url}")
            print(f"    {type(exc).__name__}: {str(exc)[:160]}")
            total_f += len(cases)
            continue
        total_p += p
        total_f += f

    print(f"\n{total_p} passed, {total_f} failed")
    return 0 if total_f == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
