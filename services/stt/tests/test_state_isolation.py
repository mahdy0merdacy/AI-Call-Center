"""Does the shared-model state swap actually isolate concurrent calls?

The claim in engine.py is that one model instance can serve N calls if the three
per-call attributes are swapped around each inference. That claim is worth
evidence, because if it is wrong the failure mode is subtle: transcripts that are
*plausible* but contaminated by another caller's audio.

Method: transcribe two different audio files
  (a) one after the other, each with its own state  -> reference
  (b) interleaved chunk-by-chunk through ONE engine -> test

If the swap works, (b) == (a) exactly. If state leaks, (b) drifts.

    python tests/test_state_isolation.py
"""

import asyncio
import sys
import wave
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.engine import SharedEngine  # noqa: E402

SPIKES = HERE.parents[2] / "spikes"
AUDIO_A = SPIKES / "simul-whisper" / "espeak_16k1.wav"
AUDIO_B = SPIKES / "simul-whisper" / "flite_16k1.wav"
MODEL = Path.home() / ".cache/whisper/small.pt"


def load(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def chunks_of(engine, audio):
    seg = engine.new_segmenter()
    out = list(seg.feed(audio))
    tail = seg.flush()
    if tail is not None and len(tail) > 0:
        out.append(tail)
    return out


async def run_alone(engine, audio):
    state = engine.new_state()
    text = ""
    for i, c in enumerate(chunks_of(engine, audio)):
        text, _, _ = await engine.transcribe(state, c, is_last=False)
    return text


async def run_interleaved(engine, audio_a, audio_b):
    """Alternate chunks between two sessions through the same engine."""
    sa, sb = engine.new_state(), engine.new_state()
    ca, cb = chunks_of(engine, audio_a), chunks_of(engine, audio_b)
    ta = tb = ""
    for i in range(max(len(ca), len(cb))):
        if i < len(ca):
            ta, _, _ = await engine.transcribe(sa, ca[i], is_last=False)
        if i < len(cb):
            tb, _, _ = await engine.transcribe(sb, cb[i], is_last=False)
    return ta, tb


async def main():
    if not MODEL.exists():
        sys.exit(f"missing {MODEL}")
    for p in (AUDIO_A, AUDIO_B):
        if not p.exists():
            sys.exit(f"missing {p}")

    print("loading one shared engine…")
    engine = SharedEngine(str(MODEL), None, language="en", segment_length=0.5)
    print(f"  device={engine.device}  warm-up {engine.warm_up():.0f} ms")
    if torch.cuda.is_available():
        print(f"  VRAM after load: {torch.cuda.memory_allocated()/2**20:,.0f} MiB")

    a, b = load(AUDIO_A), load(AUDIO_B)

    print("\n--- reference: each call alone ---")
    ref_a = await run_alone(engine, a)
    ref_b = await run_alone(engine, b)
    print(f"  A: {ref_a.strip()}")
    print(f"  B: {ref_b.strip()}")

    print("\n--- test: both interleaved through ONE engine ---")
    got_a, got_b = await run_interleaved(engine, a, b)
    print(f"  A: {got_a.strip()}")
    print(f"  B: {got_b.strip()}")

    if torch.cuda.is_available():
        print(f"\n  peak VRAM for 2 concurrent calls: "
              f"{torch.cuda.max_memory_allocated()/2**20:,.0f} MiB")

    ok_a, ok_b = got_a.strip() == ref_a.strip(), got_b.strip() == ref_b.strip()
    print(f"\n  call A isolated: {'PASS' if ok_a else 'FAIL'}")
    print(f"  call B isolated: {'PASS' if ok_b else 'FAIL'}")
    return 0 if (ok_a and ok_b) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
