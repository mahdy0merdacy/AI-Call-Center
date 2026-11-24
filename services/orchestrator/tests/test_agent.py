"""Agent turns that need no LLM: decision logic, clause splitting, scripted replies.

LLM-backed turns are covered by the integration test against a fake LLM.
"""

from __future__ import annotations

import asyncio

from app.agent import MAX_CLAUSE_CHARS, MIN_CLAUSE_CHARS, Agent
from app.tools import ToolRegistry


def _agent(tenant) -> Agent:
    return Agent(api_key="", model="test", tenant=tenant,
                 tools=ToolRegistry(servers={}, allowed=set(tenant.tools)))


def _run_turn(agent: Agent, text: str):
    events: list[dict] = []
    said: list[str] = []
    asyncio.run(agent.respond(text, events.append, said.append))
    return events, said


# ── clause splitting ─────────────────────────────────────────────────────────


def test_drain_splits_on_sentence_boundary():
    clauses = list(Agent._drain("Your parcel is in Leeds today. It arrives tomorrow"))
    assert clauses == ["Your parcel is in Leeds today. "]   # tail stays buffered


def test_drain_holds_short_fragments():
    # Too short to be worth synthesising on its own.
    assert list(Agent._drain("Okay. ")) == []
    assert len("Okay. ") < MIN_CLAUSE_CHARS


def test_drain_cuts_overlong_text_on_a_space():
    text = "word " * 60
    first = next(Agent._drain(text))
    assert len(first) <= MAX_CLAUSE_CHARS
    assert not first.endswith("wor")


# ── decisions ────────────────────────────────────────────────────────────────


def test_unmatched_utterance_is_an_open_turn(tenant):
    assert _agent(tenant)._decide("what is the weather like").kind == "open"


def test_requires_flow_redirects_to_prerequisite(tenant):
    # Rescheduling needs the parcel identified first.
    action = _agent(tenant)._decide("reschedule my delivery")
    assert action.kind == "say"
    assert action.flow.intent == "track_shipment"


# ── scripted turns (no network) ──────────────────────────────────────────────


def test_scripted_turn_speaks_config_text_and_sets_endpointing(tenant):
    events, said = _run_turn(_agent(tenant), "where is my parcel")
    assert said == ["Can I take your shipment ID?"]
    types = [e["type"] for e in events]
    assert types[0] == "flow"
    assert {"type": "set_silence_ms", "value": 1400} in events
    assert types[-1] == "turn_complete"


def test_escalation_ends_the_conversation(tenant):
    agent = _agent(tenant)
    _run_turn(agent, "where is my parcel")
    for _ in range(3):
        events, said = _run_turn(agent, "no idea")
    assert said == ["Transferring you now."]
    assert {"type": "escalated"} in events
    assert agent.ended is True
