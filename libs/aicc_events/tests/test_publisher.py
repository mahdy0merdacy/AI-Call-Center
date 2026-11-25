"""The publisher's promises: framing, routing, ordering, and never blocking a call."""

from __future__ import annotations

import struct

from aicc_events import EVENTS_TOPIC, PARTIALS_TOPIC, EventPublisher, ev, new_ulid


class FakeProducer:
    """Stands in for confluent_kafka.Producer. Can be told its queue is full."""

    def __init__(self, full: bool = False):
        self.records: list[dict] = []
        self.full = full

    def produce(self, topic, key, value, on_delivery):
        if self.full:
            raise BufferError("queue full")
        self.records.append({"topic": topic, "key": key, "value": value})
        on_delivery(None, None)

    def poll(self, timeout):
        return 0

    def flush(self, timeout):
        return 0


def _publisher(producer=None, schema_ids=None) -> EventPublisher:
    return EventPublisher(ev.SERVICE_ORCHESTRATOR, producer or FakeProducer(),
                          schema_ids={EVENTS_TOPIC: 7, PARTIALS_TOPIC: 7}
                          if schema_ids is None else schema_ids,
                          instance="test-pod", build="abc123")


def _decode(value: bytes) -> tuple[int, ev.Event]:
    assert value[0] == 0, "magic byte"
    (sid,) = struct.unpack(">I", value[1:5])
    assert value[5] == 0, "message index [0] = Event"
    return sid, ev.Event.FromString(value[6:])


def test_record_uses_registry_wire_format_and_envelope():
    producer = FakeProducer()
    call = _publisher(producer).for_call("call-1", "acme-delivery")
    call.emit(turn_decided=ev.TurnDecided(turn_index=1, intent="track_shipment"))

    (rec,) = producer.records
    assert rec["topic"] == EVENTS_TOPIC
    assert rec["key"] == b"call-1"          # partition by call: per-call order
    sid, event = _decode(rec["value"])
    assert sid == 7
    assert event.call_id == "call-1" and event.tenant_id == "acme-delivery"
    assert event.source.service == ev.SERVICE_ORCHESTRATOR
    assert event.source.instance == "test-pod" and event.source.build == "abc123"
    assert len(event.event_id) == 26 and event.occurred_at_ms > 0
    assert event.turn_decided.intent == "track_shipment"


def test_partials_go_to_their_own_topic():
    producer = FakeProducer()
    call = _publisher(producer).for_call("call-1", "t")
    call.emit(partial_transcribed=ev.PartialTranscribed(text="where is"))
    assert producer.records[0]["topic"] == PARTIALS_TOPIC


def test_seq_counts_up_per_call_and_keeps_gaps_for_drops():
    producer = FakeProducer()
    pub = _publisher(producer)
    a, b = pub.for_call("a", "t"), pub.for_call("b", "t")
    a.emit(speech_started=ev.SpeechStarted())
    b.emit(speech_started=ev.SpeechStarted())
    producer.full = True
    a.emit(speech_started=ev.SpeechStarted())        # dropped: seq 2 is lost
    producer.full = False
    a.emit(speech_started=ev.SpeechStarted())

    seqs = [(_decode(r["value"])[1].call_id, _decode(r["value"])[1].seq)
            for r in producer.records]
    assert seqs == [("a", 1), ("b", 1), ("a", 3)]    # the gap reveals the loss


def test_full_queue_drops_and_counts_instead_of_raising():
    pub = _publisher(FakeProducer(full=True))
    pub.for_call("c", "t").emit(barged_in=ev.BargedIn(turn_index=2))
    assert pub.stats()["buffer_full"] == 1
    assert pub.stats()["sent"] == 0


def test_unknown_schema_drops_until_resolved():
    producer = FakeProducer()
    pub = _publisher(producer, schema_ids={})
    pub.for_call("c", "t").emit(barged_in=ev.BargedIn())
    assert producer.records == [] and pub.stats()["no_schema"] == 1


def test_bad_payload_is_counted_not_raised():
    pub = _publisher()
    call = pub.for_call("c", "t")
    call.emit(not_an_event=ev.BargedIn())
    call.emit(barged_in=ev.BargedIn(), escalated=ev.Escalated())   # two payloads
    assert pub.stats()["error"] == 2


def test_disabled_publisher_is_a_no_op():
    pub = EventPublisher(ev.SERVICE_ORCHESTRATOR)        # no producer
    assert not pub.enabled
    pub.for_call("c", "t").emit(barged_in=ev.BargedIn())
    pub.close()
    assert pub.stats()["sent"] == 0


def test_ulids_sort_by_time():
    assert new_ulid(now_ms=1_000) < new_ulid(now_ms=2_000)
    assert len({new_ulid() for _ in range(1000)}) == 1000
