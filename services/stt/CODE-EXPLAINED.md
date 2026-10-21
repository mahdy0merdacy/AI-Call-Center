# STT service — code explained

A walk through the parts that carry the design decisions. Everything here was
verified by running it; the numbers come from this machine (RTX 3050, 4 GB).

```
audio in (16 kHz mono PCM)
   │
   ├─► vad.py        is this speech? has it stopped?      CPU, ~free
   │
   ├─► gate          ── silence? drop it, run no GPU ──┐
   │                                                   │
   ├─► LiveSegmenter 16240-sample chunks                │
   │                                                   │
   └─► engine.py     shared model + per-call state swap │
                            │                           │
                     partial / final events ◄───────────┘
```

| File | Job |
| --- | --- |
| `proto/stt.proto` | The contract. Typed events, not bare strings. |
| `app/engine.py` | **One model, many calls.** The core fix. |
| `app/vad.py` | Speech detection + silence timer. |
| `app/session.py` | One call: gating, ordering, utterance boundaries. |
| `app/server.py` | gRPC front door, admission control, readiness, drain. |

---

## 1. `engine.py` — one model, many calls

This is the piece worth understanding. Everything else is plumbing.

### The problem

`PaddedAlignAttWhisper` mixes weights and per-call state in one object:

```python
self.model              # 925 MiB of weights  <- shareable, read-only
self.segments           # per-call audio buffer
self.tokens             # per-call decoder prompt
self.last_attend_frame  # per-call attention position
```

One instance per call means one copy of the weights per call. **Measured: a second
instance costs another 924 MiB.** Ten callers would be 9 GiB of duplicated weights
on a 4 GB card — impossible, and pointless, because they'd all be identical.

### The fix: swap three attributes

Hold **one** instance. Around each inference, swap this call's state in and out:

```python
def _infer_sync(self, state, chunk, is_last):
    m = self._m
    m.segments          = state.segments
    m.tokens            = state.model_tokens
    m.last_attend_frame = state.last_attend_frame
    try:
        new_toks = m.infer(chunk, is_last)
    finally:
        state.segments          = m.segments
        state.model_tokens      = m.tokens
        state.last_attend_frame = m.last_attend_frame
```

**Why exactly these three?** `refresh_segment()` in the vendored code resets
`segments`, `tokens` and `last_attend_frame` — that's the authoritative list of
what survives between `infer()` calls. Everything else that *looks* like state
isn't: `kv_cache` and `dec_attns` are cleared by `infer()` at its own exit
(simul_whisper.py:307-308), and `new_segment`/`drop_count`/`keep_count` are reset
at its top.

### Two traps in those six lines

**Trap 1 — you must swap *out*, not just in.** At line 184 the vendored code does
`self.segments = self.segments[1:]`, i.e. it **reassigns** rather than mutating
in place when the 20 s buffer scrolls. If you only swapped in, every call after
the first would silently inherit the previous call's buffer. The `finally` block
exists so a chunk that raises can't leave one call's state aliasing another's.

**Trap 2 — there are two different token lists.** I got this wrong on the first
pass:

```python
model.tokens      # list of 2-D tensors, concatenated on dim=1 — the decoder prompt
returned tokens   # 1-D (infer() squeezes at line 310) — newly emitted tokens
```

They are not interchangeable. `model.tokens` is what the decoder is conditioned
on; the return value is what you accumulate and decode into text. Mixing them up
gives a confident-looking transcript built from the wrong tensor.

### Why the lock costs nothing

```python
async with self._lock:
    text = await loop.run_in_executor(None, self._infer_sync, state, chunk, is_last)
```

Serialising all inference sounds like a bottleneck. It isn't: a GPU runs one
kernel at a time regardless, and measurement showed batching gives **no**
per-stream speedup on this hardware (143 / 140 / 142 ms per stream at batch
2 / 4 / 8). The lock buys state-swap safety for free.

`run_in_executor` matters though — inference takes ~200 ms while audio arrives
every 20 ms. On the event loop it would stall every other call on this worker.

### Verified

`tests/test_state_isolation.py` transcribes two files (a) separately and
(b) interleaved chunk-by-chunk through one engine:

```
call A isolated: PASS
call B isolated: PASS
peak VRAM for 2 concurrent calls: 1,403 MiB
```

Byte-identical transcripts, and 1,403 MiB where two instances would have cost
~3,440 MiB. **Memory is now flat with respect to concurrency**; capacity is
compute-bound instead.

---

## 2. `vad.py` — mechanism, not policy

The service answers *"did the audio go quiet?"*. It never answers *"should the
bot reply?"* — that's turn policy, and it belongs to the orchestrator, which
knows whether it just asked for a card number (be patient) or a yes/no (be quick).

The orchestrator pushes its patience down as config:

```protobuf
uint32 silence_ms_to_endpoint = 3;  // policy, set by the brain
uint32 min_speech_ms          = 4;  // ignore coughs and clicks
float  vad_threshold          = 5;
```

**Why VAD lives here and not in the orchestrator** — two reasons, and the second
is the one that would have bitten us:

1. *Gating.* Whisper must not run on silence, and the gate has to sit in front of
   the GPU work.
2. *No race.* If the orchestrator decided boundaries, audio still in flight would
   arrive after its reset message and be consumed under the old state. Here, the
   boundary and the reset are atomic.

### Silero is a batch API pretending to be streaming

```python
def __call__(self, audio, num_samples=512, context_size_samples=64):
    h = np.zeros((1, 1, 128), dtype="float32")   # fresh LSTM state EVERY call
    c = np.zeros((1, 1, 128), dtype="float32")
```

It reallocates its LSTM state on every call, so there's no carrying state between
calls. We hand it a 256 ms block (8 frames × 512 samples) at a time and accept a
cold state per block. `BLOCK_FRAMES` is the knob: bigger blocks give the model
more context but coarsen endpoint resolution.

---

## 3. `session.py` — order of operations

Three rules, each learned by getting it wrong first.

**Gate the GPU.**

```python
if self.endpointer.in_speech:
    for chunk in self.segmenter.feed(audio):
        events.append(await self._infer(chunk, is_last=False))
```

Outside an utterance the audio is dropped and costs nothing. A caller who is
listening rather than talking uses zero GPU. This is the cheapest capacity you
will ever buy.

**The final chunk must carry `is_last=True`.**

```python
tail = self.segmenter.flush()
if tail is not None and len(tail) > 0:
    ev = await self._infer(tail, is_last=True)   # <-- without True, last word is lost
```

Simul-Whisper's attention-guided truncation holds tokens back until it's sure
they won't be revised. Without `is_last`, it never releases the last ones and
**the final word of every utterance disappears** — it reads as a model quality
problem and isn't. Found in `spikes/live-transcribe`.

**Flush, then commit, then reset — in that order.** Reset first and you throw away
the words you were about to emit.

---

## 4. `server.py` — the operational rules

**Readiness is gated on warm, not on started.**

```python
await server.start()
log.info("warm-up %.0f ms", engine.warm_up())      # ~630-800 ms of CUDA kernel compilation
await health_servicer.set(SERVICE_NAME, SERVING)   # only NOW is the pod ready
```

Model load is ~2.7 s and the first inference ~750 ms. If readiness passed before
that, k8s would route a caller to a cold pod and they'd wear ~3.4 s of silence.

*(A bug I hit here: `grpc_health.v1.health.HealthServicer` is the **sync** servicer
and its `set()` returns `None` — awaiting it crashes the server at startup. The
aio server needs `grpc_health.v1.health.aio.HealthServicer`.)*

**Admission control is explicit.**

```python
if self.active >= self.max_concurrent:
    await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, ...)
```

Capacity is compute-bound: ~200 ms of GPU per call per 0.5 s of audio, so ~2–3
calls saturate this card. Refusing the next call lets the orchestrator route to
another worker. Queueing silently would just degrade everyone already connected.

**Keepalive is set explicitly.**

```python
("grpc.keepalive_time_ms", 20000),
("grpc.keepalive_permit_without_calls", 1),
```

Defaults drop idle long-lived streams across a real network. It never shows up on
loopback, so it will first appear when you move to the cluster.

---

## 5. The contract: partial vs final

The single most important thing in the proto:

```protobuf
message Partial { ... }   // unstable — display only, NEVER send to the LLM
message Final   { ... }   // committed — safe to send to the LLM
```

The inherited orchestrator fires an LLM turn on *every* STT message, which only
worked because Azure emitted one message per completed utterance. With a
fragment-emitting model that's ~20 LLM calls per sentence. Verified fix:

```
partial  +' Thank'
partial  +' you for'
...  (16 partials)
SPEECH_END after 608ms silence
FINAL    'Thank you for calling customer support. My account number is 4729...'

1 final(s) — this is what the LLM would receive
```

Sixteen partials for the UI, **one** final for the LLM.

---

## Running it

```bash
# terminal 1
../../.venv/bin/python app/server.py --port 50051

# terminal 2
../../.venv/bin/python tests/smoke_client.py          # streams a WAV at 20ms cadence
../../.venv/bin/python tests/test_state_isolation.py  # proves the state swap
```

Useful flags: `--model-path` (default `~/.cache/whisper/small.pt`), `--segment-length`
(0.5 s), `--max-concurrent` (3), `--language` (no auto-detect, must be set).

---

## Known limitations

**Utterance onset is clipped.** `min_speech_ms=200` means transcription starts
~256 ms after speech begins, so the first word can be cut — the smoke test shows
`'Thank you for calling…'` where the audio says `'Hello, thank you for calling…'`.
The fix is a pre-roll buffer: keep the ~300 ms before `speech_start` and feed it
when the utterance opens. **Not implemented yet** — it's the first thing I'd add.

**One utterance at a time per call**, which is correct, but there's no barge-in
path yet: `EndUtterance` exists in the proto and is wired to `force_end()`, but
nothing cancels an in-flight inference.

**No TLS.** `add_insecure_port` is fine on one machine; the cluster needs mTLS.

**Capacity is a guess until measured under load.** `--max-concurrent 3` comes from
arithmetic (200 ms per 500 ms of audio), not from a load test.

**The three compat shims** in `spikes/simul_compat.py` are still required — the
vendored repo targets Python 3.9 / torch 2.0 and we run 3.12 / 2.14.
