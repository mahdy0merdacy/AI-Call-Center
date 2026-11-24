"""One call: audio in, typed events out.

Ties the three pieces together and enforces the rules that were learned the hard
way in the spikes:

  * Whisper only runs while someone is actually speaking. A caller who is
    listening costs no GPU at all. This is the cheapest capacity we will ever buy.
  * The final chunk of an utterance is flushed with `is_last=True`. Without it,
    Simul-Whisper's attention-guided truncation keeps holding its last tokens and
    the final word of every utterance is silently dropped. Measured, not theory.
  * State is reset at the utterance boundary, atomically with the decision.
  * Partials and finals are different message types. The orchestrator feeds only
    finals to the LLM; partials exist so the UI can feel responsive.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import torch

from .engine import SharedEngine
from .vad import StreamingEndpointer


@dataclass
class Event:
    kind: str                # ready|speech_start|partial|speech_end|final|metrics
    text: str = ""
    delta: str = ""
    at_sample: int = 0
    silence_ms: int = 0
    infer_ms: float = 0.0
    extra: dict = field(default_factory=dict)


class CallSession:
    """Per-call state. Cheap: a VAD timer, an audio buffer and a token list."""

    def __init__(self, engine: SharedEngine, session_id: str,
                 silence_ms_to_endpoint: int = 900, min_speech_ms: int = 200,
                 vad_threshold: float = 0.5, language: str | None = None):
        self.engine = engine
        self.session_id = session_id
        self.language = (language or engine.default_language).split("-")[0].lower()
        self.state = engine.new_state(self.language)
        self.segmenter = engine.new_segmenter()
        self.endpointer = StreamingEndpointer(
            threshold=vad_threshold,
            silence_ms_to_endpoint=silence_ms_to_endpoint,
            min_speech_ms=min_speech_ms,
        )
        self.samples_seen = 0
        self.utterance_start_sample = 0
        self.infer_ms: list[float] = []
        self._expect_sample = 0

    def note_gap(self, start_sample: int) -> int:
        """Detect loss/reordering rather than transcribing across a hole."""
        gap = start_sample - self._expect_sample if self._expect_sample else 0
        self._expect_sample = start_sample
        return gap

    async def feed(self, pcm: bytes) -> list[Event]:
        """Push one audio chunk; return every event it produced."""
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._expect_sample += len(audio)
        events: list[Event] = []

        vad_events = self.endpointer.feed(audio)
        for ve in vad_events:
            if ve.kind == "speech_start":
                self.utterance_start_sample = ve.at_sample
                events.append(Event("speech_start", at_sample=ve.at_sample))

        # Gate the GPU: only buffer and transcribe while an utterance is open.
        # Outside one, the audio is dropped on the floor and costs nothing.
        if self.endpointer.in_speech:
            for chunk in self.segmenter.feed(audio):
                events.append(await self._infer(chunk, is_last=False))

        for ve in vad_events:
            if ve.kind == "speech_end":
                events.extend(await self._close_utterance(ve.at_sample, ve.silence_ms))

        self.samples_seen += len(audio)
        return [e for e in events if e is not None]

    async def _infer(self, chunk: torch.Tensor, is_last: bool) -> Event | None:
        text, delta, ms = await self.engine.transcribe(self.state, chunk, is_last=is_last)
        self.infer_ms.append(ms)
        if not delta:
            return None
        return Event("partial", text=text, delta=delta, infer_ms=ms)

    async def _close_utterance(self, at_sample: int, silence_ms: int) -> list[Event]:
        """Flush, commit, reset — in that order."""
        events: list[Event] = []

        # The flush MUST carry is_last=True or the last word never comes out.
        tail = self.segmenter.flush()
        if tail is not None and len(tail) > 0:
            ev = await self._infer(tail, is_last=True)
            if ev:
                events.append(ev)

        events.append(Event("speech_end", at_sample=at_sample, silence_ms=silence_ms))

        committed = await self.engine.end_utterance(self.state)
        self.segmenter.reset()
        if committed.strip():
            events.append(Event(
                "final",
                text=committed.strip(),
                at_sample=at_sample,
                extra={"start_sample": self.utterance_start_sample, "end_sample": at_sample},
            ))
        return events

    async def force_end(self) -> list[Event]:
        """Orchestrator forced a boundary — barge-in, timeout, or its own policy."""
        ve = self.endpointer.force_end(self.samples_seen)
        return await self._close_utterance(ve.at_sample, ve.silence_ms)

    def metrics(self) -> dict:
        ms = sorted(self.infer_ms)
        return {
            "chunks": len(ms),
            "infer_p50_ms": round(ms[len(ms) // 2], 1) if ms else 0.0,
            "infer_max_ms": round(ms[-1], 1) if ms else 0.0,
            "audio_s": round(self.samples_seen / 16000, 1),
        }
