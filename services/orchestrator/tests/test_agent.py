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


def _one(events: list[dict], type_: str) -> dict:
    (match,) = [e for e in events if e["type"] == type_]
    return match


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
    endpointing = _one(events, "set_silence_ms")
    assert endpointing["value"] == 1400 and endpointing["slot"] == "shipment_id"
    assert types[-1] == "turn_complete"


def test_escalation_ends_the_conversation(tenant):
    agent = _agent(tenant)
    _run_turn(agent, "where is my parcel")
    for _ in range(3):
        events, said = _run_turn(agent, "no idea")
    assert said == ["Transferring you now."]
    assert _one(events, "escalated")["reason"] == "max_attempts"
    assert agent.ended is True


# ── facts reported for the event log ─────────────────────────────────────────


def test_turns_are_numbered_and_routing_reports_its_score(tenant):
    agent = _agent(tenant)
    events, _ = _run_turn(agent, "where is my parcel")
    flow = _one(events, "flow")
    assert flow["turn"] == 1 and flow["route_score"] == 1.0
    assert _one(events, "turn_complete")["clauses"] == 1

    # Inside an active flow there is no routing, so no score.
    events, _ = _run_turn(agent, "no idea")
    assert _one(events, "flow")["turn"] == 2
    assert _one(events, "flow")["route_score"] is None


def test_slot_answers_are_reported_filled_or_rejected(tenant, monkeypatch):
    agent = _agent(tenant)
    _run_turn(agent, "where is my parcel")

    events, _ = _run_turn(agent, "no idea")
    rejected = _one(events, "slot")
    assert rejected["ok"] is False and rejected["attempt"] == 1
    assert rejected["utterance"] == "no idea" and rejected["max_attempts"] == 3

    # The second answer completes the slot; stop before the tool and LLM run.
    async def no_llm(*args, **kwargs):
        return ""

    monkeypatch.setattr(agent, "_llm_turn", no_llm)
    events, _ = _run_turn(agent, "it's AC4471X209")
    filled = _one(events, "slot")
    assert filled["ok"] is True and filled["attempt"] == 2
    assert filled["value"] == "AC4471X209" and filled["slot"] == "shipment_id"
