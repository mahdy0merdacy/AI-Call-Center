# Technical tips

Decisions and constraints worth re-reading before designing a service. Everything with a
number attached was measured on this hardware (RTX 3050 Laptop, 4 GB, Ryzen laptop),
not estimated — the measurement scripts live in [`spikes/`](spikes/).

Rough dates: September 2026. Re-measure before trusting a number that's gone stale.

---

## 1. GPU memory: what actually scales per user

**The mistake to avoid:** assuming each concurrent caller costs a full model's worth of VRAM.
They don't. Measured for Whisper `small` in fp32:

| | measured |
| --- | --- |
| Model **weights** | **925 MiB — loaded once, shared by every caller** |
| **Activations** per forward pass in flight | **~291 MiB** |
| A second model instance | +924 MiB — pure duplication, avoid |

### Three kinds of GPU memory

- **Weights** — parameters. Static, read-only during inference. Every concurrent user reads
  the *same* copy. This is most of your memory and it does **not** multiply per user.
- **Activations** — intermediate tensors during a forward pass. **Transient**: they exist only
  while that chunk is being processed, then are freed.
- **Persistent per-user state** — for STT, tiny (audio buffer + token list, a few MB).
  For an LLM this is the **KV cache**, and it is a different story entirely (see §2).

### Why one model serves many callers

Because activations are transient, running chunks through **one shared model sequentially**
(a queue) means only one set of activations exists at a time:

```
1 caller:  925 (weights) + 291 (in flight) = ~1.2 GiB
7 callers: 925 (weights) + 291 (in flight) = ~1.2 GiB   <- identical
```

**Memory is flat with respect to concurrency. What scales is GPU time, not GPU memory.**

The real limit is arithmetic: one `infer()` costs ~200 ms, and each caller needs one every
`segment_length`. At 0.5 s chunks that's 40% of the GPU per caller → **~2–3 concurrent calls**
on this card. At 1 s chunks, 20% each → ~5 calls.

**So concurrency here is compute-bound, not memory-bound.** The fix for more capacity is a
faster runtime or fewer inferences — not a bigger card.

### Batching helps less than you'd expect on small GPUs

Measured, Whisper `small` encoder:

| batch | time per stream |
| --- | --- |
| 2 | 143 ms |
| 4 | 140 ms |
| 8 | 142 ms |

**Flat.** An RTX 3050 is already saturated by a single request, so batching buys shared
weights (memory) but no speed. On a datacenter A100/H100, batch-1 leaves the GPU ~90% idle and
batching gives near-linear throughput gains — that's *why cloud inference is cheap*. Do not
assume batching wins on laptop-class hardware.

### How production actually gets cheap

One model instance serving many streams; continuous batching on GPUs big enough that batch-1
wastes capacity; a fast runtime (ctranslate2/TensorRT rather than eager PyTorch — faster-whisper
measured ~12× cheaper per inference than Simul-Whisper's PyTorch fp32); and **VAD so that a
caller who is listening rather than talking costs nothing**.

---

## 2. The LLM is the opposite case

For the LLM on the 12 GB desktop, **per-user VRAM is real**. Each conversation holds a KV cache
— attention keys/values for every token so far — which persists for the whole conversation and
grows with context length. That genuinely is hundreds of MB to GB *per concurrent user*.

It's why LLM serving stacks (vLLM, TensorRT-LLM) are built around KV-cache paging and eviction,
and why LLM concurrency costs far more than STT concurrency.

> **Do not carry the STT mental model to the LLM.** STT: share aggressively, memory flat.
> LLM: per-user memory is real, budget for it.

---

## 3. What the STT service must own now that Azure is gone

### The hidden dependency

`_stt_loop` in [`voice-pipeline/Orchestrator/core/actor.py`](voice-pipeline/Orchestrator/core/actor.py)
fires an LLM turn on **every** message from STT:

```python
transcript = await self.stt.recv_text()
if transcript:
    self.full_transcript = transcript.strip()        # replaces, not appends
    asyncio.create_task(self._handle_turn_end(...))  # fires an LLM turn
```

This works only because Azure's `recognized` event fires **once per completed utterance** —
Azure's endpointer decides when the speaker stopped. One message = one turn.

**Simul-Whisper has no endpointer.** It emits a fragment every ~0.5 s (`' Hello, thank'`,
`' you'`, `' for'`). Wired in unchanged you get an LLM turn *and* a TTS synthesis every half
second, each prompted with a two-word fragment — roughly 20 LLM calls per sentence, all garbage.

**Endpointing is therefore the single most important new job for the STT service.**

### Full list of what Azure was doing invisibly

| Responsibility | Azure | Local models need |
| --- | --- | --- |
| Endpointing / turn detection | built-in | VAD + silence timeout; emit `final` only then |
| partial vs final | `recognizing` vs `recognized` | explicit message type — wire protocol is bare strings today |
| Per-utterance state reset | automatic | `refresh_segment(complete=True)` between turns or state leaks |
| Model lifecycle | n/a | 2.7 s load + 0.7 s warm-up, **before** readiness passes |
| Concurrency | effectively unlimited | bounded by local GPU (see §1) |
| Language | auto-detect | must be configured; Simul-Whisper has no auto-detect |
| Overload behaviour | absorbed by Azure | must reject or queue explicitly |

### Keep partials flowing to the client

Azure *felt* faster largely because it showed unstable partials immediately and revised them.
Simul-Whisper only emits tokens it won't retract (append-only), which feels chunkier. Send
**partials for display, finals for the LLM** and you recover the responsiveness without
triggering spurious turns.

---

## 4. gRPC: local dev vs the cluster

Target topology: 4 laptops (3 with GPU, 1 without) + 1 desktop (12 GB VRAM, hosts the main
LLM), k8s on top. See §5 for node allocation.

### Shape of the contract

```protobuf
rpc Transcribe(stream AudioChunk) returns (stream Transcript);
```

**The stream is the session.** A call is one long-lived bidirectional stream, so its audio stays
pinned to the model instance handling it — session affinity comes free. Still put `session_id`,
a sequence number and a timestamp in `AudioChunk` so loss and reordering are detectable.

### The gotcha that will bite in k8s

gRPC multiplexes over **one long-lived HTTP/2 connection**, and a normal ClusterIP Service
load-balances at **L4 — per connection, not per request**. Every stream from one orchestrator
pod lands on the *same* STT pod. One laptop's GPU saturates while the others idle.

Fix with a headless Service + client-side `round_robin` (`dns:///stt:50051`), or a service mesh
doing L7 balancing. **Decide early** — it changes how channels are constructed.

### GPU allocation is whole-device

k8s gives a pod an entire GPU via `nvidia.com/gpu: 1`. You cannot split a 4 GB card across two
pods without MPS or time-slicing. Combined with §1, this means:

> **One pod per GPU, holding one shared model and an internal queue of per-call sessions.**
> Not one pod per call. Design for the internal session pool from the start — retrofitting hurts.

### Local vs distributed checklist

| | local | cluster |
| --- | --- | --- |
| Targets | `localhost:port` | k8s DNS — **never hardcode**, use env vars (the existing `STT_URL` pattern is right) |
| Keepalive | irrelevant | set `grpc.keepalive_time_ms`; defaults drop idle streams over a real network |
| Deadlines | — | **no deadline on a call-length stream** — easy to copy from unary examples and cut calls off mid-conversation |
| Max message size | default fine | set explicitly rather than relying on the 4 MB default |
| TLS | insecure channel | mTLS between nodes — abstract channel construction behind one factory now |
| Health | — | standard `grpc.health.v1`; k8s 1.24+ probes gRPC natively |
| Readiness | — | **gate on model-loaded-and-warm**, not process-up, or the first call to a fresh pod eats 3.4 s |
| Nodes | stable | laptops sleep and roam — k3s, taints/tolerations, expect nodes to vanish |
| LLM placement | — | `nodeSelector` pinning it to the 12 GB desktop |

Bandwidth is a non-issue at this scale: raw 16 kHz mono PCM is ~256 kbps per call.

**What stays identical:** code against env-var targets and generated stubs and local
`docker compose` runs the same path as the cluster. The differences are all in *channel
configuration and scheduling*, not service logic.

---

## 5. Control plane vs data plane

**The oldest lesson in telephony:** in SIP, signalling goes through proxies — INVITE, routing,
registration, all mediated. **RTP media does not.** It goes direct or through a dedicated media
server, never through the signalling box. Not for latency reasons: the two planes have
incompatible scaling and failure characteristics, and mixing them gives you the worst of both.

"API gateway, services don't know each other" is right for **control**. Applied to **media** it
costs you:

- **Deploy coupling** — dialogue logic and the media relay in one process means every prompt
  tweak redeploys the thing carrying live calls. Every deploy drops calls.
- **Event-loop contention** — one asyncio loop carrying audio relay + LLM token streaming +
  TTS bytes + Redis I/O. Python is single-threaded here and audio is the latency-sensitive one.
- **Double network traversal** — orchestrator on node A, STT on node B means audio crosses the
  wire twice instead of once.
- **Scaling coupling** — you size one component for `max(media throughput, logic complexity)`
  instead of sizing each independently.

### Target topology

```
1. client → orchestrator      "start a call"          control, once
2. orchestrator → client      "send media to <stt-worker-2>"
3. client ⇄ media edge/STT    audio                   direct
4. STT → orchestrator         transcripts             text, ~10 msgs/turn
5. orchestrator → LLM → TTS   text                    the brain
6. TTS → client               audio                   direct
```

The orchestrator keeps everything that makes it the brain — session lifecycle, dialogue state,
turn policy, worker selection, barge-in decisions. **It stops carrying bytes it never reads.**

**Never put audio through Redis.** Redis is for durable session state, not transit bytes that are
worthless three seconds later. A `RedisMailbox` carrying audio chunks is the first thing that
falls over under load.

### Node allocation (5 nodes)

| Node | Role |
| --- | --- |
| Desktop, 12 GB VRAM | main LLM |
| 3× laptop with GPU | STT + TTS workers |
| Laptop without GPU | media edge, orchestrator, Redis, k8s control plane |

The GPU-less laptop is a natural fit for the media edge: terminating WebRTC is I/O-bound, not
compute-bound.

### Media edge: build a gateway, not an SFU

- **SFU/MCU** — selective forwarding and mixing for multi-party conferencing. Large, hard, and
  **not what a call centre needs** — calls are 1:1 (caller ↔ bot).
- **Media gateway** — terminates one WebRTC session, converts to PCM, forwards over gRPC.
  `spikes/live-transcribe/` already does roughly this.

Build the gateway. It's where the real WebRTC learning is (ICE, DTLS, jitter buffer, RTP) without
the conferencing complexity that doesn't apply here.

---

## 6. Bugs found in the inherited orchestrator

Reviewed September 2026, `voice-pipeline/Orchestrator/`. Keep the actor-per-session model and
Redis-for-history — those are sound. Fix these:

**Overlapping turns are not safe.** `_stt_loop` fires
`asyncio.create_task(self._handle_turn_end(...))` per STT message, commented *"safe with prompt
snapshots"*. The snapshot prevents a data race on the variable but **not semantic interleaving**:
two concurrent turns stream tokens into the same `output_queue`, so the client receives two
responses interleaved token by token. Rare with Azure (one event per utterance), constant with
fragment-emitting STT.

**TTS end-of-stream is a 2-second `recv` timeout.** That's up to 2 s of dead air at the end of
every turn. Needs an explicit end-of-stream marker.

**`full_transcript = transcript.strip()` replaces despite the docstring saying "Accumulate".**
Harmless with one-utterance-per-event; silently wrong the moment STT emits fragments.

**Barge-in is structurally impossible.** Once `_handle_turn_end` starts streaming TTS, nothing can
cancel it. Cancellation has to be designed in, not bolted on.

---

## 7. TTS: Magpie, and why it shapes the service

`nvidia/magpie_tts_multilingual_357m` via NVIDIA's **NeMo-Speech.cpp** (ggml C++
runtime), not the NeMo Python toolkit — the toolkit pulls a large dependency tree
that would fight the `torch 2.14+cu130` our STT needs. The C++ runtime touches
nothing in `.venv`.

| | CPU (Ryzen 5 5600H) | **GPU (RTX 3050)** |
| --- | --- | --- |
| Effective RTF | ~1.30 — **slower than realtime, unusable** | **~0.45** |
| Peak VRAM | — | **~1036 MiB** |

**The CUDA build is mandatory.** At 0.8× realtime the CPU build produces 0.8 s of
speech per second of compute, so streaming playback underruns immediately.

### It does not stream

`/v1/audio/speech` measured **TTFB 2.498 s against a total of 2.499 s** — a 0.5 ms
gap, i.e. the whole WAV is synthesized then sent.

| | time to first audio |
| --- | --- |
| whole 134-char reply in one request | **3.25 s** |
| first clause only | **~0.6 s** |

So **split replies into clauses and synthesize sequentially**. Because RTF (0.45) is
below 1, once playback starts synthesis outpaces it and the buffer only grows. This
is why `services/media-edge/agent.py` chunks on sentence boundaries.

Unlike Whisper (fixed 30 s window, flat cost), **TTS cost scales with text length** —
so keeping the agent's replies short is a real latency lever, not just style.

Other constraints: five fixed speakers (Aria, Jason, John, Leo, Sofia), **no voice
cloning** — NVIDIA removed it for security reasons, so a custom brand voice is not
possible. Each CLI invocation reloads ~500 MiB and costs ~1 s, so the model must stay
resident via `serve` or the C API rather than shelling out per utterance.

---

## 8. Where the end-to-end latency actually goes

Measured, caller stops speaking → first audio from the bot: **1.59 s**, whole turn
2.59 s.

| stage | cost | notes |
| --- | --- | --- |
| endpoint detection | **900 ms** | pure policy — `--silence-ms`. The single biggest slice. |
| STT final | ~200 ms | last chunk + flush |
| LLM first tokens | ~300–500 ms | varies with model and load |
| TTS first clause | ~600 ms | clause-chunked; whole-reply would be 3+ s |

**The dominant cost is the silence timer, not inference.** 600 ms felt snappy but
committed mid-sentence on natural speech; 900 ms is the current compromise. Anything
that beats this needs semantic endpointing — deciding the caller is *done* rather than
merely *paused* — which is the orchestrator's job, not STT's.

---

## 9. Gotchas that cost real time

**`.env.example` is not gitignored.** The root `.gitignore` has `.env`, `.env.*`,
then `!.env.example`, which **un-ignores** it. A key placed there would be committed.
`git check-ignore -v` printing the negation rule looks like a pass — verify with
`git check-ignore -q <path>; echo $?` (1 = not ignored) or `git add -n`.

**Groq retired `llama-3.3-70b-versatile`** — the model the inherited pipeline
hardcodes. It returns *"does not exist or you do not have access"*. Current usable
chat models on this key: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`,
`qwen/qwen3.8-27b`, `allam-2-7b`. We use **`qwen/qwen3.8-27b`**; `gpt-oss-20b`
returns empty content because it spends the token budget on a hidden reasoning field.

A 27B is also far closer to what a 12 GB desktop can run than a 70B, so prompts
tuned against it have some chance of transferring to local inference.

**GitHub release CDN** stalls full-file GETs from this network at 0 bytes while
serving ranged requests fine. See `spikes/magpie-tts/scripts/chunked_dl.sh`. Always
verify size + `tar tzf` before extracting — two overlapping downloads once produced a
110 MiB file that looked plausible and was corrupt.

---

## 10. Known rough edges in the current loop

Working but clunky, roughly in the order worth fixing:

1. **Utterance onset is clipped.** `min_speech_ms=200` eats ~256 ms, so the first word
   of each utterance is often lost ("Hello, thank you" → "Thank you"). Needs a
   pre-roll buffer: retain the ~300 ms before `speech_start` and feed it when the
   utterance opens.
2. **900 ms of dead air** before the bot starts. See §8 — needs semantic endpointing.
3. **Audible seams between clauses.** Each clause is synthesized independently, so
   prosody resets at the join.
4. **The bot hears itself** without headphones. There is no echo cancellation on the
   playback path, so its own voice triggers barge-in.
5. **No speculative start.** The turn only begins after the final transcript; nothing
   warms the LLM on a partial.
6. **Releasing a port is not exiting.** A service can drain, log `stopped`, free its
   port and still hold ~1.8 GiB of VRAM: gRPC pollers and the CUDA runtime leave
   non-daemon threads that never join, so the interpreter waits forever at exit. In
   k8s the pod would hang for its whole termination grace period with the GPU still
   reserved. Fixed in STT with an explicit `os._exit(0)` after the drain — worth
   doing in every GPU service, and worth verifying with
   `nvidia-smi --query-compute-apps` rather than trusting the log line.
7. **The agent lives in the media edge.** `agent.py` is deliberately a separate module
   so it can move to the orchestrator, but today the edge is doing dialogue work it
   should not own — exactly the control/data-plane mixing §5 warns about.

---

## 11. Measured facts worth not re-deriving

From [`spikes/webrtc-audio/`](spikes/webrtc-audio/README.md):

- Browser mic over WebRTC arrives as **48 kHz stereo `s16`, 960 samples (20 ms), `(1, 1920)`
  interleaved** — the stereo is fake, aiortc's Opus decoder always emits 2 channels.
- **aiortc's default STUN server costs 5 s of call setup** if unreachable. Use
  `RTCConfiguration(iceServers=[])` for localhost/LAN. 5137 ms → 141 ms.
- `frame.pts` is monotonic with no gaps, even across true silence and with DTX negotiated.
- 48k→16k resample is ~80 µs per frame — 0.4% of the frame budget. Not a concern.
- **Warm every library path before the first frame.** A 142 ms first `to_ndarray()` inside the
  recv loop showed up as fake audio jitter.

From [`spikes/simul-whisper/`](spikes/simul-whisper/) and [`spikes/live-transcribe/`](spikes/live-transcribe/README.md):

- Simul-Whisper `base` CPU: **RTF 0.89–1.03x — cannot keep up.** GPU: **0.12x**.
- `small` @ 0.5 s chunks on GPU: p50 197 ms per chunk, ~2 updates/sec, comfortable.
- **Going through the Opus codec costs accuracy.** `base` transcribed "billing" correctly from
  a file and "**willing**" over WebRTC, reproducibly. `small` fixed it. **Benchmark on
  codec-degraded audio, not on clean WAVs** — offline WER flatters you.
- `is_last=True` must be set on the final flushed chunk or **the last word of every call is
  silently dropped**. Looks like a model quality problem; isn't.
- No `tiny` CIF checkpoint exists — `base` is the floor for Simul-Whisper.

---

## 12. Open decisions

- Does endpointing live in the STT service (recommended — it owns the audio) or the orchestrator?
- Draft the `.proto` before building either side?
- Simul-Whisper vs a faster-whisper-based streamer (~12× cheaper per inference, and a
  partial-revision policy closer to how Azure felt).
- Refactor `PaddedAlignAttWhisper` so one shared model backs N lightweight session objects —
  currently weights and per-call state are conflated in one class, which is what forces
  one-instance-per-call today.
