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
  * The onset is kept. The VAD announces speech only after min_speech_ms of it,
    and it scores audio in 256 ms blocks, so by the time an utterance "starts"
    its first ~0.2-0.45 s has already gone by. Dropping that audio cut the first
    word of every utterance ("where is my parcel" -> "my parcel", "zero one four
    two" -> "four two"), which is fatal for IDs read aloud. The last PRE_ROLL of
    idle audio is held and handed to Whisper ahead of the utterance.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import torch

from .engine import SharedEngine
from .vad import BLOCK_SAMPLES, SAMPLE_RATE, StreamingEndpointer

# Idle audio held for the onset: the VAD's min_speech_ms, plus one scoring block
# of detection latency, plus a margin for soft onsets the VAD scores below its
# threshold. Leading silence costs Whisper nothing; a lost first word costs a turn.
PRE_ROLL_MARGIN_MS = 250


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
        # Idle audio, newest last, trimmed to pre_roll_samples (see PRE_ROLL_MARGIN_MS).
        self.pre_roll_samples = (min_speech_ms + PRE_ROLL_MARGIN_MS) * SAMPLE_RATE // 1000 \
            + BLOCK_SAMPLES
        self._idle: deque[np.ndarray] = deque()
        self._idle_len = 0

    def _hold_idle(self, audio: np.ndarray) -> None:
        self._idle.append(audio)
        self._idle_len += len(audio)
        while self._idle and self._idle_len - len(self._idle[0]) >= self.pre_roll_samples:
            self._idle_len -= len(self._idle.popleft())

    def _take_pre_roll(self) -> np.ndarray:
        audio = np.concatenate(self._idle) if self._idle else np.zeros(0, dtype=np.float32)
        self._idle.clear()
        self._idle_len = 0
        return audio[-self.pre_roll_samples:]

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
        started = False
        for ve in vad_events:
            if ve.kind == "speech_start":
                started = True
                self.utterance_start_sample = ve.at_sample
                events.append(Event("speech_start", at_sample=ve.at_sample))

        # Gate the GPU: only transcribe while an utterance is open. Outside one,
        # audio is only held for the next onset (a fixed ~0.7 s) and costs no GPU.
        if self.endpointer.in_speech:
            # `audio` itself stays as received: samples_seen counts the wire.
            heard = np.concatenate([self._take_pre_roll(), audio]) if started else audio
            for chunk in self.segmenter.feed(heard):
                events.append(await self._infer(chunk, is_last=False))
        else:
            self._hold_idle(audio)

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
