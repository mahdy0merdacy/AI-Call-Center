"""Offline: how much does the 48k -> 16k step cost, and can it be cheaper?

The live spike measures the real path (48k stereo -> 16k mono), but aiortc's Opus
decoder always emits stereo even when the sender is mono, so that path resamples a
duplicated channel for nothing. This compares the options on synthetic frames
shaped exactly like the real ones.

    python bench_resample.py
"""

import statistics
import time

import av
import numpy as np

SAMPLES, SRC, DST, RUNS = 960, 48000, 16000, 2000


def make_frame(layout):
    f = av.AudioFrame(format="s16", layout=layout, samples=SAMPLES)
    f.sample_rate = SRC
    f.pts = 0
    n = SAMPLES * len(f.layout.channels)
    tone = (np.sin(2 * np.pi * 440 * np.arange(n) / SRC) * 8000).astype(np.int16)
    f.planes[0].update(tone.tobytes())
    return f


def bench(label, fn, warm=200):
    for _ in range(warm):
        fn()
    times = []
    for _ in range(RUNS):
        t0 = time.perf_counter_ns()
        fn()
        times.append((time.perf_counter_ns() - t0) / 1000)
    times.sort()
    print(f"  {label:<44} mean {statistics.fmean(times):6.1f} us   "
          f"p50 {times[len(times)//2]:6.1f}   p95 {times[int(len(times)*.95)]:6.1f}")
    return statistics.fmean(times)


def main():
    stereo, mono = make_frame("stereo"), make_frame("mono")
    r_stereo = av.AudioResampler(format="s16", layout="mono", rate=DST)
    r_mono = av.AudioResampler(format="s16", layout="mono", rate=DST)

    print(f"\n{SAMPLES} samples @ {SRC} Hz -> {DST} Hz, {RUNS} runs\n")

    a = bench("AudioResampler 48k stereo -> 16k mono", lambda: r_stereo.resample(stereo))
    b = bench("AudioResampler 48k mono   -> 16k mono", lambda: r_mono.resample(mono))

    # what the pipeline would actually do: drop the duplicate channel first
    arr = stereo.to_ndarray()

    def deinterleave_then_resample():
        left = np.ascontiguousarray(arr[0, 0::2])
        f = av.AudioFrame(format="s16", layout="mono", samples=SAMPLES)
        f.sample_rate = SRC
        f.pts = 0
        f.planes[0].update(left.tobytes())
        return r_mono.resample(f)

    c = bench("deinterleave L + AudioResampler mono", deinterleave_then_resample)
    d = bench("naive decimate [::3] (aliases — reference only)",
              lambda: np.ascontiguousarray(arr[0, 0::2][::3]))

    print(f"\n  stereo->mono vs mono->mono: {a / b:.2f}x")
    print(f"  best real option: {'deinterleave+mono' if c < a else 'stereo->mono direct'} "
          f"({min(a, c):.1f} us)")
    print(f"  unfiltered decimation floor: {d:.1f} us (do not ship — no anti-alias filter)\n")


if __name__ == "__main__":
    main()
