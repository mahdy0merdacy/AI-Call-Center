"""MCP server standing in for Acme Delivery's backend.

Today it reads a JSON file. Eventually it will be a containerised service in front
of the company's real database — but the *contract* the orchestrator sees does not
change, which is the whole point of putting MCP at this boundary.

    python acme_delivery.py --port 9101        # streamable-http
    python acme_delivery.py --stdio            # for local inspection

Design notes that matter for a voice call:

  * Every tool returns a STRUCTURED result with an explicit `outcome` field that
    matches the tenant config's `outcomes:` keys. The orchestrator branches on that
    field; the LLM only phrases it. A tool that returned prose would put the model
    in charge of deciding what happened, which is exactly what we do not want.
  * Tools are fast and side-effect-light. An unbounded call on a live call is dead
    air — the orchestrator gives these a hard timeout.
  * Nothing here trusts the caller. `shipment_id` is validated again server-side;
    the orchestrator's slot pattern is the first gate, not the only one.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

FIXTURES = Path(__file__).resolve().parents[2] / "tenants" / "fixtures" / "shipments.json"
SHIPMENT_RE = re.compile(r"^[A-Za-z0-9]{10}$")

mcp = MCPServer(
    name="acme-delivery",
    instructions=(
        "Parcel tracking for Acme Delivery. Look up a shipment by its ten-character "
        "ID, or reschedule a delivery that has not yet gone out."
    ),
)


def _load() -> dict[str, Any]:
    data = json.loads(FIXTURES.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


@mcp.tool(
    name="lookup_shipment",
    description=(
        "Get the current status of a parcel by its shipment ID. "
        "Returns outcome: delivered | in_transit | delayed | not_found."
    ),
)
def lookup_shipment(shipment_id: str) -> dict[str, Any]:
    """Look up one shipment.

    Args:
        shipment_id: Ten alphanumeric characters, e.g. AC4471X209.
    """
    sid = (shipment_id or "").strip().upper()

    # Validate server-side too. The orchestrator's slot pattern already checked
    # this, but a tool that assumes its caller validated is a tool waiting to be
    # called by something else.
    if not SHIPMENT_RE.match(sid):
        return {"outcome": "not_found", "reason": "malformed_id",
                "detail": "Shipment IDs are ten letters and numbers."}

    rec = _load().get(sid)
    if rec is None:
        return {"outcome": "not_found", "shipment_id": sid}

    # Never return the recipient's address — the tenant guardrail says status only,
    # and the safest place to enforce that is where the data lives.
    out = {k: v for k, v in rec.items() if not k.startswith("_")}
    out["shipment_id"] = sid
    return out


@mcp.tool(
    name="reschedule_delivery",
    description=(
        "Move a parcel's delivery to a new date. "
        "Returns outcome: ok | too_late | not_found."
    ),
)
def reschedule_delivery(shipment_id: str, new_date: str) -> dict[str, Any]:
    """Reschedule a delivery.

    Args:
        shipment_id: Ten alphanumeric characters.
        new_date: ISO date, e.g. 2026-10-05.
    """
    sid = (shipment_id or "").strip().upper()
    rec = _load().get(sid)
    if rec is None:
        return {"outcome": "not_found", "shipment_id": sid}

    if rec.get("outcome") == "delivered":
        return {"outcome": "too_late", "reason": "already_delivered", "shipment_id": sid}
    if rec.get("reschedulable") is False or rec.get("location") == "out for delivery":
        return {"outcome": "too_late", "reason": "out_for_delivery", "shipment_id": sid}

    # A real backend would write here. The fixture is read-only on purpose: a demo
    # that mutates state is a demo that behaves differently the second time.
    return {"outcome": "ok", "shipment_id": sid, "new_date": new_date,
            "previous_estimate": rec.get("estimated_delivery")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=9101)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--stdio", action="store_true", help="stdio instead of HTTP")
    args = ap.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        # mcp 2.x: host/port are run() kwargs, not settings fields.
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
