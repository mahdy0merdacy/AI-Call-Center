"""Per-call state machine: call events in, emotion events out. No Kafka, no torch.

Inputs it reads from calls.events.v1:
  UtteranceTranscribed  -> classify it (skipped if `ignored` or empty)
  SlotRejected          -> slot_failures += 1   (model input, cumulative in the call)
  BargedIn              -> the NEXT utterance gets barge_in=True
  CallEnded             -> emit the call summary and forget the call

Delivery is at-least-once, so an input event can arrive twice. Each call
remembers the event_ids it has seen and ignores repeats; the output event_id is
derived from the input's, so a repeat that slips through still carries the same id
and downstream dedupe on event_id absorbs it.

Known limits (state is in memory):
  - After a restart a call in progress loses its slot_failures count and `seq`
    restarts at 1. Fine for a first version; persist if it matters.
  - STT and the orchestrator are different producers, so a BargedIn can arrive
    slightly before or after its utterance. Accepted: the model only sees a hint.
"""
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from aicallcenter.events.v1 import events_pb2 as ev

from app.summary import summarize

# (text, slot_failures, barge_in) -> (emotion, score)
Classify = Callable[[str, int, bool], tuple[str, float]]

DEFAULT_TTL_S = 3600  # a call silent for this long is summarised and dropped


@dataclass
class CallState:
    tenant_id: str
    last_seen: float
    utterances: int = 0
    slot_failures: int = 0
    pending_barge: bool = False
    seq: int = 0
    emotions: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)


class CallTracker:
    def __init__(self, classify: Classify, model: str, ttl_s: float = DEFAULT_TTL_S,
                 clock: Callable[[], float] = time.monotonic,
                 now_ms: Callable[[], int] = lambda: int(time.time() * 1000)):
        self._classify = classify
        self._model = model
        self._ttl = ttl_s
        self._clock = clock
        self._now_ms = now_ms
        self._calls: dict[str, CallState] = {}
        self._source = ev.Source(service=ev.SERVICE_SENTIMENT, instance=socket.gethostname(),
                                 build=os.environ.get("BUILD_SHA", "dev"))

    def __len__(self) -> int:
        return len(self._calls)

    def handle(self, event: ev.Event) -> list[ev.Event]:
        kind = event.WhichOneof("payload")
        if kind not in ("utterance_transcribed", "slot_rejected", "barged_in", "call_ended"):
            return []
        state = self._calls.get(event.call_id)
        if state is None:
            state = self._calls[event.call_id] = CallState(event.tenant_id, self._clock())
        if event.event_id in state.seen:
            return []
        state.seen.add(event.event_id)
        state.last_seen = self._clock()

        if kind == "slot_rejected":
            state.slot_failures += 1
        elif kind == "barged_in":
            state.pending_barge = True
        elif kind == "utterance_transcribed":
            return self._utterance(event, state)
        else:  # call_ended
            return self._finish(event.call_id, event.occurred_at_ms)
        return []

    def expire(self) -> list[ev.Event]:
        """Summaries for calls that went quiet without a CallEnded."""
        cutoff = self._clock() - self._ttl
        stale = [cid for cid, s in self._calls.items() if s.last_seen < cutoff]
        out: list[ev.Event] = []
        for cid in stale:
            out += self._finish(cid, self._now_ms())
        return out

    # ── internals ──────────────────────────────────────────────────────────

    def _utterance(self, event: ev.Event, state: CallState) -> list[ev.Event]:
        u = event.utterance_transcribed
        if u.ignored or not u.text.strip():
            return []
        barge = state.pending_barge
        emotion, score = self._classify(u.text, state.slot_failures, barge)
        out = self._envelope(event.call_id, state, event.occurred_at_ms,
                             f"{event.event_id}.emotion")
        out.emotion_classified.CopyFrom(ev.EmotionClassified(
            utterance_index=state.utterances, utterance_event_id=event.event_id,
            emotion=emotion, score=score, slot_failures=state.slot_failures,
            barge_in=barge, model=self._model))
        state.emotions.append(emotion)
        state.utterances += 1
        state.pending_barge = False
        return [out]

    def _finish(self, call_id: str, occurred_at_ms: int) -> list[ev.Event]:
        state = self._calls.pop(call_id, None)
        if state is None or not state.emotions:
            return []
        s = summarize(state.emotions)
        out = self._envelope(call_id, state, occurred_at_ms, f"{call_id}.emotion-summary")
        out.call_emotion_summarized.CopyFrom(ev.CallEmotionSummarized(
            utterances=s["turns"], dominant=s["dominant"], first=s["first"], last=s["last"],
            trend=s["trend"], escalation_risk=s["escalation_risk"], model=self._model))
        return [out]

    def _envelope(self, call_id: str, state: CallState, occurred_at_ms: int,
                  event_id: str) -> ev.Event:
        state.seq += 1
        return ev.Event(event_id=event_id, call_id=call_id, tenant_id=state.tenant_id,
                        source=self._source, seq=state.seq, occurred_at_ms=occurred_at_ms)
