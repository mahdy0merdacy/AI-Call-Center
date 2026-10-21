# AICallCenter

Working name. Real-time voice call handling: browser/phone audio in, STT → LLM → TTS, audio back out.

## Layout

| Path                 | What it is                                                                  |
| -------------------- | --------------------------------------------------------------------------- |
| `RUNNING.md`         | How to start every service and spike. Ports, flags, troubleshooting.         |
| `TECHNICAL-TIPS.md`  | Measured constraints and design decisions. Read before designing a service.  |
| `BIG-DATA.md`        | Data platform plan (Kafka, Postgres, Airflow, Databricks) and inventory of the data the app produces. |
| `DDIA.md`            | Why each data-platform decision was made, mapped to *Designing Data-Intensive Applications*. |
| `voice-pipeline/`    | Copied in from an earlier project (Azure-based). Reference only, not wired up. |
| `simul_whisper/`     | Clone of [backspacetg/simul_whisper](https://github.com/backspacetg/simul_whisper). Kept pristine — all compatibility patches live in `spikes/simul_compat.py`. |
| `services/`          | The real services: `stt/` (gRPC, GPU) and `media-edge/` (WebRTC, no GPU).   |
| `spikes/`            | Throwaway experiments that answer one question each. See each spike README.  |

## Spikes

- [`spikes/webrtc-audio/`](spikes/webrtc-audio/README.md) — what browser mic audio actually looks
  like on the aiortc side: frame geometry, arrival jitter, 48k→16k resample cost, PTS behaviour,
  signalling latency. Found the 5 s STUN trap.
- [`spikes/simul-whisper/`](spikes/simul-whisper/) — Simul-Whisper as a streaming transcriber:
  per-chunk cost, CPU vs GPU, WER, VRAM. CPU can't keep up (RTF ~1.0x); GPU can (0.12x).
- [`spikes/live-transcribe/`](spikes/live-transcribe/README.md) — the two joined: mic → WebRTC →
  Simul-Whisper → live transcript in the browser over a data channel.
- [`spikes/magpie-tts/`](spikes/magpie-tts/README.md) — NVIDIA Magpie TTS: install, verification by
  round-tripping through our own STT, CPU vs GPU, and the finding that it does not stream.

## Services

- [`services/stt/`](services/stt/CODE-EXPLAINED.md) — streaming STT over gRPC. One shared model
  serves N calls; VAD gating, endpointing, partial/final events. **2 concurrent calls** measured
  on a 4 GB card.
- [`services/media-edge/`](services/media-edge/README.md) — terminates WebRTC, forwards PCM to STT
  over gRPC, streams transcripts and spoken replies back on the data channel. No GPU.
  Currently also hosts [`agent.py`](services/media-edge/agent.py) (LLM + TTS turn handling) —
  that belongs to the orchestrator once it exists.
- **TTS** — Magpie via `nemo-speech serve` on `:8083`. Not a service we wrote; see
  [`spikes/magpie-tts/`](spikes/magpie-tts/README.md).

See [`RUNNING.md`](RUNNING.md) to start them.

**The loop works end to end today**: talk into http://localhost:8082 and the agent replies out
loud — 1.59 s from the end of your speech to the first audio back. Rough edges are catalogued in
[`TECHNICAL-TIPS.md`](TECHNICAL-TIPS.md) §10.

## Environment

Single venv at `.venv`, Python 3.12, holding the whole stack (aiortc, faster-whisper, torch
2.14.0+cu130). GPU: RTX 3050 Laptop, 4 GB.

Target deployment is 4 laptops (3 with GPU) + 1 desktop (12 GB VRAM, hosts the main LLM) under k8s, with
gRPC between services — see `TECHNICAL-TIPS.md` §4 for what differs from local dev.
