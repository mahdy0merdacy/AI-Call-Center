"""Fault injector: calls that are broken on purpose, for the quality checks to catch.

    .venv/bin/python data/simulator/faults.py                 # one of each
    .venv/bin/python data/simulator/faults.py duplicate seq_gap

The orchestrator does not produce these (that is the point of testing it), so
this writes small synthetic calls straight to calls.events.v1, through the same
publisher library, framing and schema the services use. Every fault is one
thing real systems do:

  duplicate        an event delivered twice (a producer retry without idempotence)
  seq_gap          a call missing seq 3 (an event lost between producer and broker)
  missing_end      a call that never ended (a crashed process; started 2 h ago)
  clock_skew       events stamped 15 minutes in the future (a node with a bad clock)
  unknown_tenant   a tenant_id no config defines (a misrouted or forged call)
  llm_outlier      a 95-second time-to-first-token (broken instrumentation)
  identifier_text  a phone number and an email spoken in free text (must be masked)
  garbage          bytes that are not our wire format at all (must be quarantined)

Every fault call is marked synthetic, channel "fault", source instance
"fault-injector": provenance for whoever investigates the check that fires.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs"))

from aicc_events import EVENTS_TOPIC, EventPublisher, ev, new_ulid

TENANT = "acme-delivery"


class FaultCall:
    """A call written event by event, with full control of the envelope."""

    def __init__(self, pub: EventPublisher, tenant: str = TENANT, start_ms: int | None = None):
        self.pub = pub
        self.call_id = new_ulid()
        self.tenant = tenant
        self.seq = 0
        # A minute back, so 400 ms steps never run past "now": only the
        # clock_skew fault should look like it came from the future.
        self.t = start_ms or int(time.time() * 1000) - 60_000

    def event(self, *, skip_seq: bool = False, skew_ms: int = 0, **payload) -> ev.Event:
        self.seq += 2 if skip_seq else 1
        self.t += 400
        e = ev.Event(event_id=new_ulid(), call_id=self.call_id, tenant_id=self.tenant,
                     source=ev.Source(service=ev.SERVICE_ORCHESTRATOR,
                                      instance="fault-injector", build="faults"),
                     seq=self.seq, occurred_at_ms=self.t + skew_ms)
        ((name, message),) = payload.items()
        getattr(e, name).CopyFrom(message)
        return e

    def send(self, e: ev.Event) -> ev.Event:
        self.pub.publish(e)
        return e

    def started(self, **kw):
        return self.send(self.event(call_started=ev.CallStarted(
            tenant_slug="acme", locale="en-US", synthetic=True, channel="fault"), **kw))

    def turn(self, n: int, text: str = "Can I take your shipment ID?",
             skip_seq: bool = False, **kw):
        self.send(self.event(turn_decided=ev.TurnDecided(
            turn_index=n, kind=ev.TURN_KIND_SAY, intent="track_shipment"),
            skip_seq=skip_seq, **kw))
        return self.send(self.event(turn_completed=ev.TurnCompleted(
            turn_index=n, total_ms=5, clauses=1, spoken_text=text), **kw))

    def ended(self, turns: int = 1, **kw):
        return self.send(self.event(call_ended=ev.CallEnded(
            reason=ev.END_REASON_CALLER_HUNG_UP, duration_ms=3000, turns=turns), **kw))


def duplicate(pub):
    c = FaultCall(pub)
    c.started()
    c.send(c.turn(1))          # the same event, sent a second time
    c.ended()


def seq_gap(pub):
    c = FaultCall(pub)
    c.started()
    c.turn(1, skip_seq=True)   # TurnDecided gets seq 3: seq 2 never existed
    c.ended()


def missing_end(pub):
    c = FaultCall(pub, start_ms=int(time.time() * 1000) - 2 * 3600 * 1000)
    c.started()
    c.turn(1)                  # ... and nothing after


def clock_skew(pub):
    c = FaultCall(pub)
    c.started(skew_ms=15 * 60 * 1000)
    c.turn(1)
    c.ended()


def unknown_tenant(pub):
    c = FaultCall(pub, tenant="ghost-co")
    c.started()
    c.turn(1)
    c.ended()


def llm_outlier(pub):
    c = FaultCall(pub)
    c.started()
    c.send(c.event(llm_completed=ev.LlmCompleted(
        turn_index=1, model="sim", purpose="open", ttft_ms=95_000, total_ms=96_000,
        prompt_tokens=300, completion_tokens=20)))
    c.turn(1, text="Sorry for the wait.")
    c.ended()


def identifier_text(pub):
    c = FaultCall(pub)
    c.started()
    c.turn(1, text="I've noted 01 42 88 77 11 and will email jane.doe@example.com.")
    c.send(c.event(slot_rejected=ev.SlotRejected(
        turn_index=2, intent="track_shipment", slot="shipment_id", attempt=1,
        max_attempts=3, utterance="my number is 0142887711")))
    c.ended(turns=2)


def garbage(pub):
    # Not through publish(): that would frame it correctly. Raw bytes, as a
    # misconfigured producer pointed at the wrong topic would send.
    pub._producer.produce(EVENTS_TOPIC, key=b"garbage",
                          value=b'{"oops": "this is JSON, not protobuf"}')


FAULTS = {f.__name__: f for f in (duplicate, seq_gap, missing_end, clock_skew,
                                  unknown_tenant, llm_outlier, identifier_text, garbage)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("faults", nargs="*", metavar="FAULT",
                    help=f"any of: {', '.join(FAULTS)} (default: all)")
    args = ap.parse_args()
    if unknown := set(args.faults) - set(FAULTS):
        ap.error(f"unknown fault(s): {', '.join(sorted(unknown))}")

    pub = EventPublisher.from_env(ev.SERVICE_ORCHESTRATOR)
    if not pub.enabled:
        sys.exit("set KAFKA_BOOTSTRAP (and SCHEMA_REGISTRY_URL) to inject faults")
    time.sleep(1)               # let the schema id resolve before publishing
    for name in args.faults or FAULTS:
        FAULTS[name](pub)
        print(f"injected {name}")
    pub.close()
    print(pub.stats())


if __name__ == "__main__":
    main()
