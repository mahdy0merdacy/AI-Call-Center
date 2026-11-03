# Spike: live transcription, mic → WebRTC → Simul-Whisper → browser

**Question:** do the three pieces actually compose? Browser mic audio over WebRTC,
into Simul-Whisper, with partial text coming back to the page mid-utterance.

Answer: yes, comfortably — with one accuracy cost that only an end-to-end test exposes.

```bash
# small model, 0.5 s chunks — the config that actually sounds acceptable
../../.venv/bin/python server.py --model-path ~/.cache/whisper/small.pt --segment-length 0.5

# then open http://localhost:8081
```

`base` at 1 s chunks is the fallback if VRAM is needed elsewhere, but it is noticeably
worse — see the model comparison below.

Click **Start mic** and talk. Text appears about twice per second at word granularity,
newest fragment highlighted, with live infer-time and backlog counters underneath.

## How it fits together

```
browser mic ──WebRTC/Opus──▶ aiortc track.recv()
                                  │  48 kHz stereo s16, 960 samples, every 20 ms
                                  ▼
                            av.AudioResampler  ──▶ 16 kHz mono float32
                                  ▼
                            LiveSegmenter      ──▶ 16240-sample chunks
                                  ▼                 (240 carried + 16000 new)
                            Simul-Whisper.infer()   in a thread executor
                                  ▼
                            RTCDataChannel     ──▶ partial text in the page
```

Transcripts go back over the **same peer connection** on a data channel, so there's
no second socket and no session correlation to get wrong.

### Three things that matter

**Inference runs in a thread executor.** It takes ~200 ms (`small`) while `recv()`
delivers a frame every 20 ms. Running it inline would stall the receive loop for ten
frames' worth of audio on every chunk and back up the jitter buffer.

**The model is warmed at startup** (~700 ms). The first inference pays CUDA kernel
compilation — `spikes/simul-whisper` measured a 630 ms first chunk against a ~95 ms
steady state. Paying that on a caller's first word is the same mistake the WebRTC
spike caught in its own instrumentation.

**`LiveSegmenter` reproduces `SegmentWrapper` exactly** — 16240-sample chunks made of
a 240-sample tail from the previous chunk plus 16000 new samples. That tail is how
Simul-Whisper gets the same CNN context at chunk boundaries that it gets offline.
Get it wrong and streaming quality silently degrades from the paper's numbers.

## Measured

Headless runs, RTX 3050 Laptop (4 GB), ~10 s of speech through the full WebRTC path.

| | `base`, 1 s chunks | **`small`, 0.5 s chunks** |
| --- | --- | --- |
| Inference per chunk | p50 94 ms / max 112 ms | **p50 197 ms / max 255 ms** |
| Chunk budget | 1000 ms | **500 ms** |
| Backlog | 0 throughout | **0 throughout** |
| Updates per second | ~1 | **~2** |
| Peak VRAM | ~470 MiB | **~1720 MiB** |
| Transcript | "my **willing** address" | "my **billing** address" ✓ |

`small` at half the chunk length still leaves ~2.5x headroom on the budget, and fits
the 4 GB card with room for the desktop's ~690 MiB. It is the config to use.

Text arrives at word granularity:

```
+' Hello, thank'   (196 ms)   +'. My account'  (202 ms)
+' you'            (185 ms)   +' number is'    (205 ms)
+' for'            (189 ms)   +' 4729. I'      (235 ms)
+' calling'        (187 ms)   +' my billing'   (210 ms)
```

### The cost of going through WebRTC

`base` transcribed the same audio correctly from a file but produced "my **willing**
address" over WebRTC, reproducibly across two runs. Opus-encoding at 48 kHz, decoding
and resampling to 16 kHz was enough to turn a plosive into a glide.

`small` gets it right through the same path. The lesson survives the fix:
**offline WER on clean files flatters you** — a model that scores 0.0% on a WAV can
still fail on the codec-degraded version of that same audio. Benchmark on audio that
has been through the codec, not on the source file.

### Bug worth remembering: `is_last`

The first working version passed `is_last=False` for every chunk, including the final
flushed one. Simul-Whisper's attention-guided truncation then never released the
tokens it was holding, and **the last word of every call was silently dropped** —
"…update my billing address" with the trailing "please." missing. It looks like a
model quality problem and is not. The final flush must set `is_last=True`.

## Limits

**One call at a time.** `PaddedAlignAttWhisper` holds per-utterance state (`segments`,
`tokens`), so a second caller would corrupt the first one's transcript. The server
returns HTTP 409 rather than allowing it. Concurrency needs one model instance per
call, so on this 4 GB card that is roughly **2 concurrent calls with `small`**
(~1.7 GiB each) or ~6 with `base` (~0.5 GiB each) — memory-bound either way.

**No utterance segmentation.** `refresh_segment(complete=True)` is called when the call
ends, not when the speaker finishes a sentence. A real deployment needs VAD-driven
turn detection to decide when an utterance is over and the LLM should respond.

**No barge-in, no TTS, nothing downstream.** This spike ends at the transcript.

## Compatibility shims

The cloned repo targets Python 3.9 / PyTorch 2.0; we run 3.12 / torch 2.14. Three
patches live in [`../simul_compat.py`](../simul_compat.py), shared with the
`simul-whisper` spike. **The clone itself is untouched and still pullable.**

1. `torchaudio.load` routes through torchcodec since 2.9 → fall back to `soundfile`
2. The shipped CIF checkpoints were saved on CUDA → `torch.load` needs `map_location` on CPU
3. whisper's Triton median-filter kernel rewrites `JITFunction.src`, which Triton 3
   forbids; `timing.py` only catches `RuntimeError` so its own fallback never fires →
   bind the pure-torch path instead
