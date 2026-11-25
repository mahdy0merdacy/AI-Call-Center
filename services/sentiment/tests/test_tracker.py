import pytest
from aicallcenter.events.v1 import events_pb2 as ev

from app.tracker import CallTracker
from app.wire import NotOurFormat, frame, unframe

CALLS: list[tuple[str, int, bool]] = []


def fake_classify(text: str, fails: int, barge: bool) -> tuple[str, float]:
    CALLS.append((text, fails, barge))
    return ("angry", 0.9) if "!" in text else ("neutral", 0.8)


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def tracker(clock):
    CALLS.clear()
    return CallTracker(fake_classify, model="v-test", ttl_s=100, clock=clock,
                       now_ms=lambda: 42)


def event(eid: str, call: str = "c1", **payload) -> ev.Event:
    return ev.Event(event_id=eid, call_id=call, tenant_id="acme", occurred_at_ms=1000,
                    **payload)


def said(eid: str, text: str, call: str = "c1", ignored: bool = False) -> ev.Event:
    return event(eid, call, utterance_transcribed=ev.UtteranceTranscribed(
        text=text, ignored=ignored))


def test_utterance_is_classified_and_enveloped(tracker):
    [out] = tracker.handle(said("e1", "hello"))
    e = out.emotion_classified
    assert (e.emotion, e.utterance_index, e.utterance_event_id, e.model) == \
        ("neutral", 0, "e1", "v-test")
    assert out.event_id == "e1.emotion"
    assert out.call_id == "c1" and out.tenant_id == "acme"
    assert out.source.service == ev.SERVICE_SENTIMENT
    assert out.occurred_at_ms == 1000 and out.seq == 1


def test_signals_reach_the_model(tracker):
    tracker.handle(event("r1", slot_rejected=ev.SlotRejected()))
    tracker.handle(event("r2", slot_rejected=ev.SlotRejected()))
    tracker.handle(event("b1", barged_in=ev.BargedIn()))
    tracker.handle(said("e1", "first"))
    tracker.handle(said("e2", "second"))
    assert CALLS == [("first", 2, True), ("second", 2, False)]  # barge applies once


def test_ignored_and_empty_utterances_are_skipped(tracker):
    assert tracker.handle(said("e1", "[MUSIC]", ignored=True)) == []
    assert tracker.handle(said("e2", "   ")) == []
    assert CALLS == []


def test_redelivered_event_is_ignored(tracker):
    assert len(tracker.handle(said("e1", "hello"))) == 1
    assert tracker.handle(said("e1", "hello")) == []
    tracker.handle(event("r1", slot_rejected=ev.SlotRejected()))
    tracker.handle(event("r1", slot_rejected=ev.SlotRejected()))  # retry: counted once
    tracker.handle(said("e2", "next"))
    assert CALLS[-1][1] == 1


def test_call_ended_emits_summary_and_forgets_call(tracker):
    tracker.handle(said("e1", "hello"))
    tracker.handle(said("e2", "this is wrong!"))
    tracker.handle(said("e3", "fix it now!"))
    [s] = tracker.handle(event("end", call_ended=ev.CallEnded()))
    c = s.call_emotion_summarized
    assert (c.utterances, c.first, c.last, c.dominant) == (3, "neutral", "angry", "angry")
    assert c.trend == "worsening" and c.escalation_risk
    assert s.seq == 4 and s.event_id == "c1.emotion-summary"
    assert len(tracker) == 0


def test_call_without_utterances_has_no_summary(tracker):
    assert tracker.handle(event("end", call_ended=ev.CallEnded())) == []


def test_calls_do_not_mix(tracker):
    tracker.handle(event("r1", "a", slot_rejected=ev.SlotRejected()))
    tracker.handle(said("e1", "hi", "b"))
    assert CALLS == [("hi", 0, False)]


def test_irrelevant_events_are_ignored(tracker):
    assert tracker.handle(event("x", tool_invoked=ev.ToolInvoked())) == []
    assert len(tracker) == 0


def test_quiet_call_is_summarised_after_ttl(tracker, clock):
    tracker.handle(said("e1", "hello"))
    clock.now = 50
    assert tracker.expire() == []
    clock.now = 101
    [s] = tracker.expire()
    assert s.call_emotion_summarized.utterances == 1 and s.occurred_at_ms == 42
    assert len(tracker) == 0


def test_wire_round_trip():
    e = said("e1", "hello")
    assert unframe(frame(7, e)) == e
    assert frame(7, e)[:6] == b"\x00\x00\x00\x00\x07\x00"


@pytest.mark.parametrize("raw", [b"", b"{}", b"\x01\x00\x00\x00\x07\x00", b"\x00\x00\x00\x00\x07\x00\xff\xff"])
def test_wire_rejects_garbage(raw):
    with pytest.raises(NotOurFormat):
        unframe(raw)
