"""A small, self-contained tenant, so unit tests don't move when demo configs do.

Shaped like tenants/acme-delivery.yaml: one slot-gated flow with a tool, one flow
that requires it, one internal flow that must never be routed to directly.
"""

from __future__ import annotations

import pytest

from app.flow import Tenant, load_tenants

TENANT_YAML = """
tenant_id: test-co
display_name: Test Co
slug: test
locale: en-US
voice:
  speaker: Aria
  greeting: "Test Co, how can I help?"
persona: You are a test assistant.
endpointing:
  silence_ms: 900
flows:
  - intent: track_shipment
    utterances: ["where is my parcel", "track my delivery"]
    slots:
      - name: shipment_id
        type: alphanumeric
        pattern: '^[A-Za-z0-9]{10}$'
        prompt: "Can I take your shipment ID?"
        reprompt: "I need the full ten-character ID."
        on_invalid: "That doesn't look right."
        max_attempts: 3
        endpointing:
          silence_ms: 1400
    tool: lookup_shipment
  - intent: reschedule_delivery
    utterances: ["reschedule my delivery"]
    requires_flow: track_shipment
    tool: reschedule_delivery
  - intent: secret_step
    internal: true
    utterances: ["where is my parcel"]
tools: [lookup_shipment, reschedule_delivery]
knowledge:
  sources: [kb.md]
escalation:
  message: "Transferring you now."
"""


@pytest.fixture
def tenant(tmp_path) -> Tenant:
    (tmp_path / "test.yaml").write_text(TENANT_YAML)
    (tmp_path / "kb.md").write_text("Opening hours: 9 to 5.")
    return load_tenants(tmp_path)["test"]
