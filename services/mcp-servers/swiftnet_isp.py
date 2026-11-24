"""MCP server standing in for SwiftNet's backend.

One tool, four outcomes — and the outcome changes the shape of the rest of the
conversation, not just its wording. That is the point this tenant demonstrates:

    line_ok            -> start troubleshooting
    area_outage        -> do NOT troubleshoot, it is not their equipment
    account_suspended  -> transfer, do not discuss the amount
    not_found          -> re-confirm the number

    python swiftnet_isp.py --port 9102
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

FIXTURES = Path(__file__).resolve().parents[2] / "tenants" / "fixtures" / "lines.json"
PHONE_RE = re.compile(r"^[0-9]{10}$")

mcp = MCPServer(
    name="swiftnet-isp",
    instructions=(
        "Broadband line diagnostics for SwiftNet. Check the status of a line by the "
        "account's landline number."
    ),
)


def _load() -> dict[str, Any]:
    data = json.loads(FIXTURES.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


@mcp.tool(
    name="check_line_status",
    description=(
        "Check a broadband line by the landline number on the account. "
        "Returns outcome: line_ok | area_outage | account_suspended | not_found."
    ),
)
def check_line_status(phone_number: str) -> dict[str, Any]:
    """Diagnose one line.

    Args:
        phone_number: Ten digits, no spaces or country code.
    """
    # Callers read numbers aloud, so strip whatever formatting arrives.
    num = re.sub(r"\D", "", phone_number or "")

    if not PHONE_RE.match(num):
        return {"outcome": "not_found", "reason": "malformed_number",
                "detail": "Account numbers are ten digits."}

    rec = _load().get(num)
    if rec is None:
        return {"outcome": "not_found", "phone_number": num}

    out = {k: v for k, v in rec.items() if not k.startswith("_")}
    out["phone_number"] = num

    # Deliberately withhold the billing amount even though a real backend would
    # have it. The tenant guardrail says transfer to accounts; a tool that returns
    # the figure invites the model to read it out.
    if out.get("outcome") == "account_suspended":
        out.pop("balance", None)
        out.pop("amount_due", None)

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=9102)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--stdio", action="store_true")
    args = ap.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        # mcp 2.x: host/port are run() kwargs, not settings fields.
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
