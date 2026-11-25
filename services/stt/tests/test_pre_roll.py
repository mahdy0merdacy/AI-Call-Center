"""The onset of an utterance reaches Whisper.

The VAD only says "speech" after min_speech_ms of it, scored in 256 ms blocks, so
the utterance's first ~0.2-0.45 s has already been fed by then. These tests pin
that the session hands that audio to the segmenter instead of dropping it, while
idle audio stays bounded and never reaches the GPU on its own.

No model, no GPU: the VAD and the engine are fakes.

    ../../.venv/bin/python -m pytest tests/test_pre_roll.py -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import session as session_mod  # noqa: E402
from app.vad import VadEvent  # noqa: E402

CHUNK = 320                                  # 20 ms at 16 kHz, what the edge sends


class FakeEndpointer:
    """Says "speech" on chunk `start_at`, "silence" on chunk `end_at`."""

    script: dict[int, str] = {}

    def __init__(self, **_):
        self.in_speech = False
        self.n = 0

    def feed(self, samples):
        kind = self.script.get(self.n)
        self.n += 1
        if kind == "start":
            self.in_speech = True
            return [VadEvent("speech_start", self.n * CHUNK)]
        if kind == "end":
            self.in_speech = False
            return [VadEvent("speech_end", self.n * CHUNK, silence_ms=900)]
        return []

    def force_end(self, at_sample):
        self.in_speech = False
        return VadEvent("speech_end", at_sample)


class RecordingSegmenter:
    def __init__(self):
        self.fed: list[np.ndarray] = []

    def feed(self, audio):
        self.fed.append(np.array(audio))
        return []

    def flush(self):
        return None

    def reset(self):
        pass


class FakeEngine:
    default_language = "en"

    def __init__(self):
        self.segmenter = RecordingSegmenter()

    def new_state(self, language):
        return object()

    def new_segmenter(self):
        return self.segmenter

    async def transcribe(self, state, chunk, is_last):
        return "", "", 0.0

    async def end_utterance(self, state):
        return ""


def _pcm(value: int) -> bytes:
    """One 20 ms chunk whose every sample is `value`, so its origin is traceable."""
    return np.full(CHUNK, value, dtype=np.int16).tobytes()


def _run(script: dict[int, str], chunks: int, min_speech_ms: int = 200):
    FakeEndpointer.script = script
    engine = FakeEngine()
    original = session_mod.StreamingEndpointer
    session_mod.StreamingEndpointer = FakeEndpointer
    try:
        s = session_mod.CallSession(engine, "t", min_speech_ms=min_speech_ms)

        async def go():
            for i in range(chunks):
                await s.feed(_pcm(i + 1))

        asyncio.run(go())
    finally:
        session_mod.StreamingEndpointer = original
    return s, engine.segmenter.fed


def _origins(audio: np.ndarray) -> list[int]:
    """Which chunks (1-based) an array was assembled from, in order."""
    values = np.round(audio * 32768).astype(int)
    out: list[int] = []
    for v in values:
        if not out or out[-1] != v:
            out.append(int(v))
    return out


def test_the_onset_before_the_vad_fired_is_handed_to_whisper():
    # 60 idle chunks (1.2 s), speech declared on chunk 61.
    s, fed = _run({60: "start"}, chunks=61)
    first = fed[0]
    held = s.pre_roll_samples
    assert len(first) == held + CHUNK                  # the whole pre-roll + this chunk
    origins = _origins(first)
    assert origins[-1] == 61                           # the chunk where speech was declared
    assert origins[-2] == 60                           # ...right after the audio before it
    # ~0.7 s back: min_speech_ms + one VAD block + margin, not the whole idle period.
    assert 0.6 <= held / 16000 <= 0.8
    assert origins[0] > 1


def test_a_call_that_starts_talking_at_once_loses_nothing():
    s, fed = _run({3: "start"}, chunks=4)
    assert _origins(fed[0]) == [1, 2, 3, 4]


def test_idle_audio_is_bounded_and_never_transcribed():
    s, fed = _run({}, chunks=500)                      # 10 s of listening
    assert fed == []                                   # no GPU while nobody speaks
    assert s._idle_len <= s.pre_roll_samples + CHUNK


def test_samples_seen_counts_the_wire_not_the_pre_roll():
    s, _ = _run({60: "start"}, chunks=70)
    assert s.samples_seen == 70 * CHUNK


def test_after_an_utterance_the_next_onset_gets_its_own_pre_roll():
    s, fed = _run({20: "start", 40: "end", 80: "start"}, chunks=81)
    second_start = next(a for a in fed[1:] if len(a) > CHUNK)
    origins = _origins(second_start)
    assert origins[-1] == 81
    assert 41 <= origins[0] < 81                       # only audio from after the first one
