"""Fire-and-forget publishing of call events to Kafka.

A call must never wait on analytics. So publishing is asynchronous end to end:

  - `emit()` builds the record and hands it to librdkafka's in-memory queue,
    which returns immediately. A background thread delivers it.
  - The queue is bounded. When it is full (Kafka down, network slow), the event
    is DROPPED AND COUNTED instead of blocking or growing until the pod is
    killed. A dead dashboard is acceptable; a stalled call is not.
  - Nothing here raises into the caller. A bug in event building is logged and
    counted, and the call carries on.

Delivery is at-least-once (the producer retries), so the same event can arrive
twice. Every event carries a unique `event_id`; consumers dedupe on it.

Records use the Schema Registry wire format: magic byte 0, the 4-byte schema
id, the message index (0 = Event, the first message in the file), then the
Protobuf bytes. The schema id is looked up once, in the background. Until it is
known, events are dropped as `no_schema`.

Configuration comes from the environment, so the same image runs anywhere:

  KAFKA_BOOTSTRAP       host:port list. Unset = publishing disabled (local dev, tests).
  SCHEMA_REGISTRY_URL   default http://localhost:8085
  SERVICE_BUILD         git SHA of the running code, default "dev"
  POD_NAME              instance name; falls back to the hostname
"""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
import time
import urllib.request
from typing import Any

from . import events_pb2 as ev
from .ulid import new_ulid

log = logging.getLogger("events")

EVENTS_TOPIC = "calls.events.v1"
PARTIALS_TOPIC = "calls.partials.v1"

# High-volume, low-value payloads get their own topic and retention.
_TOPIC_FOR = {"partial_transcribed": PARTIALS_TOPIC}
_PAYLOADS = {f.name for f in ev.Event.DESCRIPTOR.oneofs_by_name["payload"].fields}

_PRODUCER_CONFIG: dict[str, Any] = {
    # An acknowledged event is on every in-sync replica (DDIA.md §10).
    "acks": "all",
    # The broker drops retries of a record it already has, within a session.
    # event_id covers what this cannot: retries across a restart.
    "enable.idempotence": True,
    # Wait up to 20 ms to batch records: fewer requests, invisible to a call.
    "linger.ms": 20,
    "compression.type": "zstd",
    # THE bound. Past this, produce() raises BufferError and we drop.
    "queue.buffering.max.messages": 20_000,
    "queue.buffering.max.kbytes": 16_384,
    # Give up on a record after 30 s; the delivery callback counts it as failed.
    "message.timeout.ms": 30_000,
}


class EventPublisher:
    """One per process. Safe to call from an asyncio loop: nothing here blocks."""

    def __init__(self, service: int, producer: Any = None, *,
                 registry_url: str = "", schema_ids: dict[str, int] | None = None,
                 instance: str | None = None, build: str | None = None):
        self.source = ev.Source(
            service=service,
            instance=instance or os.environ.get("POD_NAME") or socket.gethostname(),
            build=build or os.environ.get("SERVICE_BUILD", "dev"),
        )
        self._producer = producer
        self._registry_url = registry_url.rstrip("/")
        self._schema_ids: dict[str, int] = dict(schema_ids or {})
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._counts = {"sent": 0, "delivered": 0, "no_schema": 0, "buffer_full": 0,
                        "delivery_failed": 0, "error": 0}

    @classmethod
    def from_env(cls, service: int) -> EventPublisher:
        bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "").strip()
        if not bootstrap:
            log.info("KAFKA_BOOTSTRAP not set: call events will not be published")
            return cls(service)

        from confluent_kafka import Producer  # only needed when publishing

        pub = cls(service, registry_url=os.environ.get("SCHEMA_REGISTRY_URL",
                                                        "http://localhost:8085"))
        name = ev.Service.Name(service).removeprefix("SERVICE_").lower()
        pub._producer = Producer({**_PRODUCER_CONFIG, "bootstrap.servers": bootstrap,
                                  "client.id": f"{name}-{pub.source.instance}"})
        pub.start()
        log.info("publishing call events to %s (registry %s)", bootstrap, pub._registry_url)
        return pub

    @property
    def enabled(self) -> bool:
        return self._producer is not None

    def start(self) -> None:
        """Background threads: deliver records, and look up schema ids."""
        threading.Thread(target=self._poll_loop, name="events-poll", daemon=True).start()
        if self._registry_url:
            threading.Thread(target=self._resolve_schemas, name="events-schema",
                             daemon=True).start()

    def for_call(self, call_id: str, tenant_id: str) -> CallEvents:
        return CallEvents(self, call_id, tenant_id)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def close(self, timeout: float = 5.0) -> None:
        """Deliver what is queued, within `timeout`. Called during shutdown drain."""
        if self._producer is not None:
            left = self._producer.flush(timeout)
            if left:
                log.warning("shutdown: %d event(s) still queued were lost", left)
        self._closed.set()
        if self.enabled:
            log.info("call events: %s", self.stats())

    # -- internals ------------------------------------------------------------

    def publish(self, event: ev.Event) -> None:
        if self._producer is None:
            return
        topic = _TOPIC_FOR.get(event.WhichOneof("payload") or "", EVENTS_TOPIC)
        sid = self._schema_ids.get(topic)
        if sid is None:
            self._count("no_schema")
            return
        # Wire format: magic 0, schema id, message index [0] encoded as one 0 byte.
        value = b"\x00" + struct.pack(">I", sid) + b"\x00" + event.SerializeToString()
        try:
            self._producer.produce(topic, key=event.call_id.encode(), value=value,
                                   on_delivery=self._on_delivery)
            self._count("sent")
        except BufferError:
            self._count("buffer_full")
        except Exception:
            log.exception("could not queue event")
            self._count("error")

    def _on_delivery(self, err: Any, _msg: Any) -> None:
        self._count("delivery_failed" if err else "delivered")
        if err:
            log.debug("event delivery failed: %s", err)

    def _count(self, what: str) -> None:
        with self._lock:
            self._counts[what] += 1
            n = self._counts[what]
        # Loss must be visible but must not flood the log: warn at 1, 10, 100...
        if what in ("no_schema", "buffer_full", "delivery_failed") and _power_of_ten(n):
            log.warning("call events dropped (%s): %d so far", what, n)

    def _poll_loop(self) -> None:
        while not self._closed.is_set():
            self._producer.poll(0.5)    # runs delivery callbacks

    def _resolve_schemas(self) -> None:
        delay = 1.0
        for topic in (EVENTS_TOPIC, PARTIALS_TOPIC):
            while topic not in self._schema_ids and not self._closed.is_set():
                url = f"{self._registry_url}/subjects/{topic}-value/versions/latest"
                try:
                    with urllib.request.urlopen(url, timeout=5) as resp:
                        self._schema_ids[topic] = int(json.load(resp)["id"])
                    log.info("schema for %s: id %d", topic, self._schema_ids[topic])
                except Exception as exc:
                    log.warning("schema lookup for %s failed (%s); retrying in %.0fs",
                                topic, exc, delay)
                    self._closed.wait(delay)
                    delay = min(delay * 2, 30.0)


class CallEvents:
    """The events of one call, as seen by one service.

    Holds the per-call sequence number. It increases even when an event is
    dropped, so a consumer that sees seq 7 then 9 knows exactly one was lost.
    """

    def __init__(self, publisher: EventPublisher, call_id: str, tenant_id: str):
        self._pub = publisher
        self.call_id = call_id
        self.tenant_id = tenant_id
        self._seq = 0

    def emit(self, **payload: Any) -> None:
        """emit(tool_invoked=ev.ToolInvoked(...)). Exactly one payload. Never raises."""
        try:
            ((name, message),) = payload.items()
            if name not in _PAYLOADS:
                raise ValueError(f"unknown event payload {name!r}")
            self._seq += 1
            event = ev.Event(event_id=new_ulid(), call_id=self.call_id,
                             tenant_id=self.tenant_id, source=self._pub.source,
                             seq=self._seq, occurred_at_ms=int(time.time() * 1000))
            getattr(event, name).CopyFrom(message)
            self._pub.publish(event)
        except Exception:
            log.exception("could not build call event %s", list(payload))
            self._pub._count("error")


def _power_of_ten(n: int) -> bool:
    while n >= 10 and n % 10 == 0:
        n //= 10
    return n == 1
