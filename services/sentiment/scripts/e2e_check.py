"""End-to-end check against a real Kafka: fake call in, emotions out.

    1. start Kafka + Schema Registry, create topics, register schemas
    2. start the worker:      python -m app.consumer
    3. run this script:       python scripts/e2e_check.py

It publishes a short call to calls.events.v1 (a greeting, then the caller getting
angrier, with two rejected slots and a barge-in), then waits up to 60 s for the
worker's answers on calls.sentiment.v1 and prints them.
"""
import json
import os
import secrets
import sys
import time
import urllib.request
from pathlib import Path

from confluent_kafka import Consumer, Producer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aicallcenter.events.v1 import events_pb2 as ev  # noqa: E402

from app.wire import frame, unframe  # noqa: E402

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
REGISTRY = os.environ.get("SCHEMA_REGISTRY", "http://localhost:8085")
IN_TOPIC, OUT_TOPIC = "calls.events.v1", "calls.sentiment.v1"


def schema_id(topic: str) -> int:
    with urllib.request.urlopen(f"{REGISTRY}/subjects/{topic}-value/versions/latest", timeout=10) as r:
        return int(json.load(r)["id"])


def main() -> int:
    call_id = f"e2e-{secrets.token_hex(6)}"
    n = 0

    def make(**payload) -> ev.Event:
        nonlocal n
        n += 1
        return ev.Event(event_id=f"{call_id}-{n}", call_id=call_id, tenant_id="acme-delivery",
                        source=ev.Source(service=ev.SERVICE_ORCHESTRATOR), seq=n,
                        occurred_at_ms=int(time.time() * 1000), **payload)

    def says(text: str) -> ev.Event:
        return make(utterance_transcribed=ev.UtteranceTranscribed(text=text, language="fr"))

    story = [
        says("Bonjour, j'appelle au sujet de ma commande."),
        make(slot_rejected=ev.SlotRejected(slot="order_id", attempt=1)),
        says("Je vous ai déjà donné mon numéro, pourquoi le répéter ?"),
        make(slot_rejected=ev.SlotRejected(slot="order_id", attempt=2)),
        make(barged_in=ev.BargedIn()),
        says("C'est inadmissible, je veux parler à votre responsable !"),
        make(call_ended=ev.CallEnded(reason=ev.END_REASON_ESCALATED, turns=3)),
    ]

    # Subscribe BEFORE producing, so nothing is missed.
    consumer = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"e2e-{call_id}",
                         "auto.offset.reset": "latest", "enable.auto.commit": False})
    consumer.subscribe([OUT_TOPIC])
    consumer.poll(3.0)  # let the group get its partitions

    in_id = schema_id(IN_TOPIC)
    producer = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all"})
    for e in story:
        producer.produce(IN_TOPIC, key=call_id.encode(), value=frame(in_id, e))
    producer.flush(10)
    print(f"sent {len(story)} events for {call_id}; waiting for the worker...")

    got: list[ev.Event] = []
    deadline = time.time() + 60
    while time.time() < deadline:
        m = consumer.poll(1.0)
        if m is None or m.error():
            continue
        e = unframe(m.value())
        if e.call_id != call_id:
            continue
        got.append(e)
        if e.WhichOneof("payload") == "emotion_classified":
            c = e.emotion_classified
            print(f"  utterance {c.utterance_index}: {c.emotion:<10} score {c.score:.3f} "
                  f"fails={c.slot_failures} barge={c.barge_in}")
        else:
            s = e.call_emotion_summarized
            print(f"  SUMMARY: dominant={s.dominant} first={s.first} last={s.last} "
                  f"trend={s.trend} escalation_risk={s.escalation_risk}")
            break
    consumer.close()

    emotions = [e.emotion_classified.emotion for e in got
                if e.WhichOneof("payload") == "emotion_classified"]
    ok = len(emotions) == 3 and got[-1].WhichOneof("payload") == "call_emotion_summarized" \
        and got[-1].call_emotion_summarized.escalation_risk
    print("E2E OK" if ok else "E2E FAILED (is the worker running?)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
