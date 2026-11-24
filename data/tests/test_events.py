"""Does the event schema keep the promises its header makes?

A round-trip test proves almost nothing about a log schema — of course a
message decodes with the code that encoded it. What matters for a log is the
cases where writer and reader are DIFFERENT versions (DDIA ch.4), so that is
what this checks:

  1. forward compat   an old reader gets a record from a newer writer
  2. backward compat  a new reader gets a record from an older writer
  3. classification   every string field declares its PII class
  4. size             what binary encoding buys over JSON, per event

    data/gen.sh && .venv/bin/python data/tests/test_events.py
"""

import json
import sys
from pathlib import Path

from google.protobuf import descriptor, json_format

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gen"))

from aicallcenter.events.v1 import events_pb2 as ev  # noqa: E402

FAILED = 0


def check(name: str, ok: bool, detail: str = ""):
    global FAILED
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    FAILED += not ok


def sample() -> ev.Event:
    return ev.Event(
        event_id="01JABCDEF0123456789XYZABCD",
        call_id="acme-01JABCDEF0123456789XYZ",
        tenant_id="acme-delivery",
        source=ev.Source(service=ev.SERVICE_ORCHESTRATOR, instance="laptop-1",
                         build="67f3b6c"),
        seq=7,
        occurred_at_ms=1_760_000_000_000,
        tool_invoked=ev.ToolInvoked(
            turn_index=2, tool="lookup_shipment", outcome="delivered",
            latency_ms=41,
            result_json='{"outcome":"delivered","signed_by":"R. Dupont"}'),
    )


# ── wire-format helpers: hand-write fields a newer schema would add ─────────

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(number: int, payload: bytes) -> bytes:
    """Length-delimited field (wire type 2): strings and sub-messages."""
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def test_forward_compat():
    print("forward compat — old reader, newer writer")

    # A v1.1 writer added (a) a top-level field 15 and (b) a new payload type 70.
    newer_field = sample().SerializeToString() + _field(15, b"eu-west")
    old = ev.Event.FromString(newer_field)
    check("unknown field does not break parsing", old.call_id.startswith("acme-"))
    check("known payload still readable", old.tool_invoked.outcome == "delivered")
    # Proto keeps unknown fields and writes them back out. This is what lets a
    # pass-through stage (enrich, redact, re-key) run old code without
    # silently stripping fields a downstream consumer already depends on.
    check("unknown field survives re-serialisation",
          old.SerializeToString().endswith(_field(15, b"eu-west")))

    base = sample()
    base.ClearField("tool_invoked")
    newer_payload = base.SerializeToString() + _field(70, b"")
    old = ev.Event.FromString(newer_payload)
    check("unknown payload type reads as 'no payload' (consumer skips it)",
          old.WhichOneof("payload") is None)


def test_backward_compat():
    print("backward compat — new reader, older writer")

    # An older writer that predates route_score and the source block.
    older = ev.Event(event_id="x", call_id="c", seq=1,
                     turn_decided=ev.TurnDecided(turn_index=1,
                                                 kind=ev.TURN_KIND_SAY))
    new = ev.Event.FromString(older.SerializeToString())
    check("absent scalar reads as zero", new.turn_decided.turn_index == 1
          and new.occurred_at_ms == 0)
    # 0.0 is a legitimate score, so "not reported" needs presence tracking —
    # that is what `optional` buys. Without it the two are indistinguishable.
    check("absent `optional` is distinguishable from 0.0",
          not new.turn_decided.HasField("route_score"))
    check("absent sub-message reads as UNSPECIFIED, not a crash",
          new.source.service == ev.SERVICE_UNSPECIFIED)


def test_pii_declared():
    print("classification — every string field declares (pii)")

    missing = []

    def walk(msg: descriptor.Descriptor):
        for f in msg.fields:
            if f.type == descriptor.FieldDescriptor.TYPE_STRING:
                if not f.GetOptions().HasExtension(ev.pii):
                    missing.append(f.full_name)
        for nested in msg.nested_types:
            walk(nested)

    for m in ev.DESCRIPTOR.message_types_by_name.values():
        walk(m)
    check("no unclassified string fields", not missing, ", ".join(missing))

    # The redaction a bronze sink will do, driven entirely by the schema.
    e = sample()
    ti = e.tool_invoked
    personal = [f.name for f, _ in ti.ListFields()
                if f.GetOptions().Extensions[ev.pii] == ev.PII_PERSONAL]
    check("result_json is found as PERSONAL from the descriptor alone",
          personal == ["result_json"])


def test_size():
    print("size — binary vs JSON, one ToolInvoked event")
    e = sample()
    binary = len(e.SerializeToString())
    as_json = len(json.dumps(json_format.MessageToDict(e)).encode())
    print(f"       protobuf {binary} B   json {as_json} B   ratio {as_json / binary:.2f}x")
    # JSON repeats every field name in every record; protobuf sends a 1-2 byte
    # tag instead. Most of what is left is the string values themselves.
    check("binary is smaller", binary < as_json)


if __name__ == "__main__":
    test_forward_compat()
    test_backward_compat()
    test_pii_declared()
    test_size()
    print("FAILED" if FAILED else "all passed")
    sys.exit(1 if FAILED else 0)
