"""End-to-end check of step 3: one real Event, through the registry, Kafka, and back.

    .venv/bin/python data/kafka/smoke.py

Produces a CallStarted for a fresh call_id to calls.events.v1, then consumes
from the beginning until it finds that event, and prints where it landed.

The framing is done by hand, on purpose, so the wire format is visible.
Every record value is:

    0x00              magic byte (Confluent wire format v0)
    <4 bytes>         schema id, big-endian — which registered schema wrote this
    <varints>         which message in the .proto file (0 = first = Event)
    <protobuf bytes>  the Event itself

Producers in step 4 will use the library serializer, which does the same.
"""

import os
import secrets
import struct
import sys
import time
import json
import urllib.request
from pathlib import Path

from confluent_kafka import Consumer, Producer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gen"))
from aicallcenter.events.v1 import events_pb2 as ev  # noqa: E402

BOOTSTRAP = "localhost:9092"
REGISTRY = "http://localhost:8085"
TOPIC = "calls.events.v1"

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """48-bit ms timestamp + 80 random bits, Crockford base32. Sorts by time."""
    n = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def schema_id() -> int:
    with urllib.request.urlopen(
            f"{REGISTRY}/subjects/{TOPIC}-value/versions/latest", timeout=5) as r:
        return json.load(r)["id"]


def frame(sid: int, msg) -> bytes:
    # Message index [0] is optimised to a single 0 byte.
    return b"\x00" + struct.pack(">I", sid) + b"\x00" + msg.SerializeToString()


def unframe(value: bytes) -> tuple[int, ev.Event]:
    assert value[0] == 0, "not Confluent wire format"
    sid = struct.unpack(">I", value[1:5])[0]
    assert value[5] == 0, "expected message index [0] (Event)"
    return sid, ev.Event.FromString(value[6:])


def main() -> int:
    sid = schema_id()
    call_id = f"smoke-{ulid()}"
    event = ev.Event(
        event_id=ulid(), call_id=call_id, tenant_id="acme-delivery",
        source=ev.Source(service=ev.SERVICE_MEDIA_EDGE,
                         instance=os.uname().nodename, build="smoke"),
        seq=1, occurred_at_ms=int(time.time() * 1000),
        call_started=ev.CallStarted(tenant_slug="acme", locale="en-US",
                                    synthetic=True, channel="smoke"))

    # acks=all + idempotence: the settings real producers will use (DDIA.md §7, §10).
    p = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all",
                  "enable.idempotence": True})
    delivered = {}
    p.produce(TOPIC, key=call_id.encode(), value=frame(sid, event),
              on_delivery=lambda err, m: delivered.update(err=err, msg=m))
    p.flush(10)
    if delivered.get("err") or "msg" not in delivered:
        print(f"produce FAILED: {delivered.get('err')}")
        return 1
    m = delivered["msg"]
    print(f"produced  {call_id}  -> partition {m.partition()} offset {m.offset()} "
          f"({len(m.value())} B, schema id {sid})")

    c = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"smoke-{os.getpid()}",
                  "auto.offset.reset": "earliest", "enable.auto.commit": False})
    c.subscribe([TOPIC])
    deadline = time.time() + 20
    try:
        while time.time() < deadline:
            msg = c.poll(1.0)
            if msg is None or msg.error():
                continue
            got_sid, got = unframe(msg.value())
            if got.event_id != event.event_id:
                continue
            _, broker_ms = msg.timestamp()
            print(f"consumed  {got.call_id}  <- partition {msg.partition()} "
                  f"offset {msg.offset()}")
            print(f"          payload={got.WhichOneof('payload')}  "
                  f"schema id {got_sid}  key={msg.key().decode()}")
            print(f"          occurred_at -> broker LogAppendTime: "
                  f"{broker_ms - got.occurred_at_ms} ms")
            ok = got == event
            print("round trip OK" if ok else "round trip MISMATCH")
            return 0 if ok else 1
    finally:
        c.close()
    print("did not find the event within 20 s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
