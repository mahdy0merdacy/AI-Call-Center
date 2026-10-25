"""Run Simul-Whisper over an audio file and measure it as a streaming transcriber.

Simul-Whisper (INTERSPEECH 2024) feeds Whisper fixed-length chunks and uses
attention-guided truncation detection to decide which tokens are safe to emit
before the utterance ends. That is the property faster-whisper lacks: it gives
partial output mid-utterance instead of only after a segment closes.

This deliberately lives outside the cloned repo so the clone stays pristine.

    python run_stream.py --audio some_16k.wav --truth some.txt

What it reports:
  * per-chunk inference time vs the chunk's own duration (can it keep up?)
  * when each token actually became visible — emission latency, the number that
    decides whether a caller hears a response quickly
  * final transcript, and WER if ground truth is given
"""

import argparse
import re
import statistics
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "stt" / "app"))

from whisper_compat import REPO, install_shims  # noqa: E402

install_shims()

from simul_whisper.transcriber.config import AlignAttConfig  # noqa: E402
from simul_whisper.transcriber.segment_loader import SegmentWrapper  # noqa: E402
from simul_whisper.transcriber.simul_whisper import (  # noqa: E402
    DEC_PAD,
    PaddedAlignAttWhisper,
)


DIGITS = "zero one two three four five six seven eight nine".split()


def normalize(text):
    """Spell out digits so '4729' scores equal to 'four seven two nine'."""
    text = text.lower()
    text = re.sub(r"\d", lambda m: f" {DIGITS[int(m.group())]} ", text)
    return re.sub(r"[^a-z ]", " ", text).split()


def wer(ref, hyp):
    import numpy as np

    r, h = normalize(ref), normalize(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=np.int32)
    d[:, 0] = np.arange(len(r) + 1)
    d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + cost)
    return d[len(r), len(h)] / max(1, len(r))


def describe(xs):
    xs = sorted(xs)
    return (f"mean {statistics.fmean(xs) * 1000:.0f} ms, "
            f"p50 {xs[len(xs) // 2] * 1000:.0f}, "
            f"p95 {xs[int(len(xs) * 0.95)] * 1000:.0f}, "
            f"max {xs[-1] * 1000:.0f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--truth", type=Path, help="ground-truth text file, for WER")
    ap.add_argument("--model-path", default=str(Path.home() / ".cache/whisper/base.pt"))
    ap.add_argument("--cif", default=None, help="CIF checkpoint (default: match model name)")
    ap.add_argument("--segment-length", type=float, default=1.0, help="chunk seconds")
    ap.add_argument("--frame-threshold", type=int, default=12)
    ap.add_argument("--buffer-len", type=float, default=20.0)
    ap.add_argument("--min-seg-len", type=float, default=0.0)
    ap.add_argument("--language", default="en")
    ap.add_argument("--quiet", action="store_true", help="hide the incremental output")
    args = ap.parse_args()

    name = Path(args.model_path).stem
    cif = args.cif or str(REPO / "cif_models" / f"{name}.pt")

    print(f"\nmodel     : {args.model_path}")
    print(f"cif       : {cif}")
    print(f"audio     : {args.audio}")
    print(f"chunk     : {args.segment_length}s   frame_threshold {args.frame_threshold}   "
          f"buffer {args.buffer_len}s")

    cfg = AlignAttConfig(
        model_path=args.model_path,
        segment_length=args.segment_length,
        frame_threshold=args.frame_threshold,
        language=args.language,
        buffer_len=args.buffer_len,
        min_seg_len=args.min_seg_len,
        if_ckpt_path=cif,
    )

    t0 = time.perf_counter()
    model = PaddedAlignAttWhisper(cfg)
    load_s = time.perf_counter() - t0
    print(f"load      : {load_s:.1f}s\n")

    segmented = SegmentWrapper(audio_path=str(args.audio), segment_length=args.segment_length)

    hyp_list, per_chunk, emissions = [], [], []
    text = ""
    wall0 = time.perf_counter()

    for seg_id, (seg, is_last) in enumerate(segmented):
        t = time.perf_counter()
        new_toks = model.infer(seg, is_last)
        dt = time.perf_counter() - t
        per_chunk.append(dt)

        hyp_list.append(new_toks)
        hyp = torch.cat(hyp_list, dim=0)
        hyp = hyp[hyp < DEC_PAD]
        new_text = model.tokenizer.decode(hyp)

        # audio time already consumed vs wall time spent getting here
        audio_t = (seg_id + 1) * args.segment_length
        if new_text != text:
            emissions.append((audio_t, time.perf_counter() - wall0, new_text))
            if not args.quiet:
                grown = new_text[len(text):]
                print(f"  [audio {audio_t:5.1f}s | wall {time.perf_counter() - wall0:5.1f}s | "
                      f"chunk {dt * 1000:4.0f} ms] +{grown!r}")
            text = new_text

    model.refresh_segment(complete=True)

    total_audio = len(per_chunk) * args.segment_length
    total_compute = sum(per_chunk)

    print(f"\n--- timing ({len(per_chunk)} chunks of {args.segment_length}s) ---")
    print(f"  per-chunk inference : {describe(per_chunk)}")
    print(f"  chunk budget        : {args.segment_length * 1000:.0f} ms")
    over = [d for d in per_chunk if d > args.segment_length]
    print(f"  chunks over budget  : {len(over)}/{len(per_chunk)}"
          + (f"  <- cannot keep up in real time" if over else "  <- keeps up"))
    print(f"  total audio {total_audio:.1f}s -> compute {total_compute:.1f}s "
          f"(RTF {total_compute / total_audio:.2f}x)")

    if emissions:
        lag = [w - a for a, w, _ in emissions]
        print(f"  emission lag (wall-audio): mean {statistics.fmean(lag):.2f}s, "
              f"max {max(lag):.2f}s")

    if torch.cuda.is_available():
        print(f"  peak VRAM           : "
              f"{torch.cuda.max_memory_allocated() / 2**20:.0f} MiB allocated, "
              f"{torch.cuda.max_memory_reserved() / 2**20:.0f} MiB reserved")

    print(f"\n--- final transcript ---\n  {text.strip()}\n")
    if args.truth and args.truth.exists():
        ref = args.truth.read_text().strip()
        print(f"  ref : {ref}")
        print(f"  WER : {wer(ref, text):.1%}\n")


if __name__ == "__main__":
    main()
