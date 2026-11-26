"""Call events: what a whole call puts on Kafka, decoded the way a consumer would.

Drives real calls through OrchestratorServicer.Session with a fake Kafka
producer, so the checks cover the agent, the session's translation of agent
facts into events, and the publisher's framing, together.
"""

from __future__ import annotations

import asyncio

from aicc_events import EVENTS_TOPIC, PARTIALS_TOPIC, EventPublisher, ev

from app.agent import Agent
from app.server import OrchestratorServicer, Session
from gen import orchestrator_pb2 as pb


class FakeProducer:
    def __init__(self):
        self.records: list[dict] = []

    def produce(self, topic, key, value, on_delivery):
        self.records.append({"topic": topic, "key": key, "value": value})

    def poll(self, timeout):
        return 0

    def flush(self, timeout):
        return 0


def _servicer(tenant, producer: FakeProducer) -> OrchestratorServicer:
    publisher = EventPublisher(ev.SERVICE_ORCHESTRATOR, producer,
                               schema_ids={EVENTS_TOPIC: 1, PARTIALS_TOPIC: 1},
                               instance="test", build="test")
    return OrchestratorServicer({"test": tenant}, groq_key="", model="test-model",
                                mcp_endpoints={}, publisher=publisher)


def _run_call(servicer: OrchestratorServicer, *messages: pb.CallEvent) -> None:
    async def requests():
        for m in messages:
            yield m
            await asyncio.sleep(0.05)     # let each turn run, like a real caller

    async def call():
        async for _ in servicer.Session(requests(), context=None):
            pass

    asyncio.run(call())


def _decoded(producer: FakeProducer) -> list[ev.Event]:
    return [ev.Event.FromString(r["value"][6:]) for r in producer.records]


START = pb.CallEvent(start=pb.StartCall(tenant_slug="test", call_id="call-1",
                                        synthetic=True, channel="simulator"))
HANGUP = pb.CallEvent(end=pb.EndCall(reason="hangup"))


def _said(text: str) -> pb.CallEvent:
    return pb.CallEvent(transcript=pb.Transcript(text=text, is_final=True))


def test_a_scripted_call_produces_its_events_in_order(tenant):
    producer = FakeProducer()
    _run_call(_servicer(tenant, producer), START, _said("where is my parcel"), HANGUP)
    events = _decoded(producer)

    assert [e.WhichOneof("payload") for e in events] == [
        "call_started",
        "turn_completed",          # the greeting, turn 0
        "utterance_transcribed",   # what the caller said: the sentiment worker's input
        "turn_decided",
        "endpointing_changed",     # the shipment-ID slot needs a longer silence window
        "turn_completed",
        "call_ended",
    ]
    assert {r["key"] for r in producer.records} == {b"call-1"}
    assert [e.seq for e in events] == [1, 2, 3, 4, 5, 6, 7]
    assert {e.tenant_id for e in events} == {"test-co"}

    started, greeting, said, decided, endpointing, turn, ended = events
    assert said.utterance_transcribed.text == "where is my parcel"
    assert said.utterance_transcribed.language == "en"
    assert started.call_started.synthetic and started.call_started.channel == "simulator"
    assert greeting.turn_completed.turn_index == 0
    assert decided.turn_decided.kind == ev.TURN_KIND_SAY
    assert decided.turn_decided.intent == "track_shipment"
    assert decided.turn_decided.HasField("route_score")
    assert endpointing.endpointing_changed.silence_ms == 1400
    assert endpointing.endpointing_changed.slot == "shipment_id"
    assert turn.turn_completed.spoken_text == "Can I take your shipment ID?"
    assert ended.call_ended.reason == ev.END_REASON_CALLER_HUNG_UP
    assert ended.call_ended.turns == 1


def test_hangup_mid_reply_cancels_the_turn_and_ends_last(tenant, monkeypatch):
    async def slow_llm(self, session, messages):
        self.llm_stats = {"ttft_ms": 0, "prompt_tokens": 0, "completion_tokens": 0}
        await asyncio.sleep(10)
        yield "never spoken"

    monkeypatch.setattr(Agent, "_stream_llm", slow_llm)
    producer = FakeProducer()
    _run_call(_servicer(tenant, producer), START, _said("what is the weather"), HANGUP)
    kinds = [e.WhichOneof("payload") for e in _decoded(producer)]

    # The LLM call is reported, the reply never completes, and nothing follows CallEnded.
    assert kinds[-2:] == ["llm_completed", "call_ended"]
    assert "turn_completed" not in kinds[2:]


class _Capture:
    def __init__(self):
        self.emitted: list[dict] = []

    def emit(self, **payload):
        self.emitted.append(payload)


def _session(tenant) -> tuple[Session, _Capture]:
    capture = _Capture()
    return Session(tenant, agent=None, call_id="c", events=capture), capture  # type: ignore[arg-type]


def test_tool_and_llm_facts_become_events(tenant):
    session, capture = _session(tenant)
    session.on_event({"type": "tool_result", "turn": 3, "tool": "lookup_shipment",
                      "outcome": "delayed", "data": {"outcome": "delayed"},
                      "latency_ms": 84, "timed_out": False})
    session.on_event({"type": "llm", "turn": 3, "model": "m", "purpose": "grounded",
                      "ttft_ms": 410, "total_ms": 900, "prompt_tokens": 512,
                      "completion_tokens": 40, "cancelled": False})

    tool = capture.emitted[0]["tool_invoked"]
    assert (tool.turn_index, tool.outcome, tool.latency_ms) == (3, "delayed", 84)
    assert tool.result_json == '{"outcome": "delayed"}'
    llm = capture.emitted[1]["llm_completed"]
    assert (llm.ttft_ms, llm.prompt_tokens, llm.purpose) == (410, 512, "grounded")


def test_slot_answers_become_filled_or_rejected_events(tenant):
    session, capture = _session(tenant)
    session.on_event({"type": "slot", "turn": 2, "intent": "track_shipment",
                      "slot": "shipment_id", "ok": False, "attempt": 1, "max_attempts": 3,
                      "value": "", "utterance": "um no idea"})
    session.on_event({"type": "slot", "turn": 3, "intent": "track_shipment",
                      "slot": "shipment_id", "ok": True, "attempt": 2, "max_attempts": 3,
                      "value": "AC4471X209", "utterance": ""})

    assert capture.emitted[0]["slot_rejected"].utterance == "um no idea"
    assert capture.emitted[1]["slot_filled"].value == "AC4471X209"
