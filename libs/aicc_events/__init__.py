"""Call events: the shared contract and publisher used by every service.

    from aicc_events import EventPublisher, ev
    pub = EventPublisher.from_env(ev.SERVICE_ORCHESTRATOR)
    call = pub.for_call(call_id, tenant_id)
    call.emit(turn_decided=ev.TurnDecided(turn_index=1, intent="track_shipment"))

`events_pb2` is generated from data/proto/.../events.proto by data/gen.sh.
"""

from . import events_pb2 as ev
from .publisher import EVENTS_TOPIC, PARTIALS_TOPIC, CallEvents, EventPublisher
from .ulid import new_ulid

__all__ = ["EVENTS_TOPIC", "PARTIALS_TOPIC", "CallEvents", "EventPublisher", "ev", "new_ulid"]
