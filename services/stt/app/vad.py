"""Voice activity detection and endpointing.

Two jobs, and it matters that they are separate:

  VAD        — is this 32 ms frame speech?          mechanism, lives here
  endpointer — has the caller stopped talking?      mechanism, lives here
  turn policy — should the bot answer now?          POLICY, lives in the orchestrator

This service reports `speech_end(silence_ms)`. It never decides that a turn is
over. The orchestrator knows the dialogue state — whether it just asked for a
16-digit card number or a yes/no — so it owns that call. It pushes its patience
down to us as `silence_ms_to_endpoint` in the stream config.

Why VAD is here rather than in the orchestrator:

  * gating. Whisper must not run on silence; that is the single biggest cost
    lever we have. The gate has to sit in front of the GPU work.
  * no race. If the orchestrator decided boundaries, audio still in flight would
    reach us after its reset message and be consumed under the wrong state.

Silero v6 detail: `SileroVADModel.__call__` allocates fresh LSTM state on every
call (faster_whisper/vad.py), so it is a batch API, not a streaming one. We hand
it a block of frames at a time and accept a cold state per block. Larger blocks
give the model more context but coarsen endpoint resolution, so BLOCK_FRAMES is
the knob for that trade.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from faster_whisper.vad import get_vad_model

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512                      # Silero's window: 32 ms at 16 kHz
FRAME_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE
BLOCK_FRAMES = 8                         # 256 ms per inference — context vs resolution
BLOCK_SAMPLES = FRAME_SAMPLES * BLOCK_FRAMES


@dataclass
class VadEvent:
    kind: str          # "speech_start" | "speech_end"
    at_sample: int
    silence_ms: int = 0


class StreamingEndpointer:
    """Frame-level VAD plus a silence timer, per call.

    Holds no model of its own — the ONNX session is shared process-wide, since
    it is stateless between calls by construction.
    """

    _model = None

    @classmethod
    def _shared_model(cls):
        if cls._model is None:
            cls._model = get_vad_model()
        return cls._model

    def __init__(self, threshold: float = 0.5, silence_ms_to_endpoint: int = 900,
                 min_speech_ms: int = 200):
        self.threshold = threshold
        self.silence_ms_to_endpoint = silence_ms_to_endpoint
        self.min_speech_ms = min_speech_ms

        self._model = self._shared_model()
        self._pending = np.zeros(0, dtype=np.float32)
        self._consumed = 0          # samples fed so far, for at_sample reporting

        self.in_speech = False
        self._speech_ms = 0         # speech accumulated in the current utterance
        self._silence_ms = 0        # trailing silence since the last speech frame

    @property
    def trailing_silence_ms(self) -> int:
        return self._silence_ms

    def feed(self, samples: np.ndarray) -> list[VadEvent]:
        """Push float32 mono audio; return whatever boundaries it crossed."""
        events: list[VadEvent] = []
        self._pending = np.concatenate([self._pending, samples])

        while len(self._pending) >= BLOCK_SAMPLES:
            block, self._pending = self._pending[:BLOCK_SAMPLES], self._pending[BLOCK_SAMPLES:]
            probs = np.asarray(self._model(block, num_samples=FRAME_SAMPLES)).reshape(-1)

            for i, p in enumerate(probs):
                at = self._consumed + i * FRAME_SAMPLES
                if p >= self.threshold:
                    self._silence_ms = 0
                    self._speech_ms += FRAME_MS
                    # Only announce speech once it has lasted long enough to not
                    # be a cough, a click or a door.
                    if not self.in_speech and self._speech_ms >= self.min_speech_ms:
                        self.in_speech = True
                        events.append(VadEvent("speech_start", at))
                else:
                    if self.in_speech:
                        self._silence_ms += FRAME_MS
                        if self._silence_ms >= self.silence_ms_to_endpoint:
                            events.append(
                                VadEvent("speech_end", at, silence_ms=self._silence_ms)
                            )
                            self.in_speech = False
                            self._speech_ms = 0
                            self._silence_ms = 0
                    else:
                        # silence outside an utterance: reset the speech counter
                        # so scattered noise never accumulates into a start
                        self._speech_ms = 0

            self._consumed += BLOCK_SAMPLES

        return events

    def force_end(self, at_sample: int) -> VadEvent:
        """Orchestrator forced a boundary (barge-in, timeout, its own policy)."""
        ev = VadEvent("speech_end", at_sample, silence_ms=self._silence_ms)
        self.in_speech = False
        self._speech_ms = 0
        self._silence_ms = 0
        return ev
