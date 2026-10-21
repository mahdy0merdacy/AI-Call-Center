# Running things

Every service and spike in this repo, how to start it, and what it needs.
All commands assume repo root at `~/Projects/AICallCenter` and the shared venv at
`.venv` (Python 3.12).

## Ports

| Port | What | Needs GPU |
| --- | --- | --- |
| 8083 | TTS — `nemo-speech serve` (Magpie) | **yes** |
| 50051 | STT service (gRPC) | **yes** |
| 8082 | Media edge (HTTP + WebRTC) — the UI | no |
| 8081 | `live-transcribe` spike (self-contained) | yes |
| 8080 | `webrtc-audio` spike (measurement only) | no |
| 9092 | Kafka broker (`data/docker-compose.yml`) | no |
| 8085 | Schema Registry | no |
| 8090 | Kafka UI | no |

## Prerequisites

```bash
# one-time: the venv already exists, but to rebuild it
python3.12 -m venv .venv
.venv/bin/pip install -r services/stt/requirements.txt
.venv/bin/pip install --index-url https://download.pytorch.org/whl/cu130 \
    torch==2.14.0+cu130 torchaudio==2.11.0+cu130
```

Models live in `~/.cache/whisper/` (`base.pt`, `small.pt`). `small` is the default
and the one worth using. The CIF heads ship with the `simul_whisper/` clone.

---

## The full loop: talk to the agent

Three terminals, in this order. Both GPU services must be warm before the edge is useful.

```bash
# terminal 1 — TTS (Magpie on GPU)
~/.local/nemo-speech/bin/nemo-speech serve --host 127.0.0.1 --port 8083 --no-ui \
  --tts-model nvidia/magpie_tts_multilingual_357m \
  --codec-model nvidia/nemo-nano-codec-22khz-1.89kbps-21.5fps \
  --device cuda

# terminal 2 — STT (Simul-Whisper on GPU)
cd services/stt
../../.venv/bin/python app/server.py --port 50051

# terminal 3 — media edge + agent (no GPU)
cd services/media-edge
../../.venv/bin/python server.py --port 8082 --stt 127.0.0.1:50051 --tts http://127.0.0.1:8083
```

Then open **http://localhost:8082**, click *Start mic*, and talk. Pause for ~900 ms
and the agent replies out loud.

Measured: **~2.5 s from the caller falling silent to first audio back**. That breaks
down as ~900 ms endpoint timer + ~200 ms STT final + 1.59 s for LLM→TTS (the 1.59 s
is measured from the final transcript, not from the end of speech). Whole turn 2.59 s.
The endpoint timer is the largest single slice — see `TECHNICAL-TIPS.md` §8.

The LLM key is read from `voice-pipeline/LLM/.env` (`GROQ_API_KEY=…`). Without it the
edge still runs but only transcribes — it logs `no GROQ_API_KEY — transcription only`.

GPU budget: STT ~1.4 GiB + TTS ~1.0 GiB = **~2.8 GiB of 4 GiB**. Both fit, with the
desktop taking ~0.7 GiB on top.

Wait for STT to log `warm-up … ms` before connecting. Readiness is deliberately
gated on that — the first inference costs ~700 ms of CUDA kernel compilation.

### Useful flags

**STT** (`services/stt/app/server.py`)

| Flag | Default | Note |
| --- | --- | --- |
| `--model-path` | `~/.cache/whisper/small.pt` | `base.pt` is faster and noticeably worse |
| `--segment-length` | `0.5` | chunk seconds; lower = more responsive, more GPU |
| `--max-concurrent` | `2` | **measured**, not guessed — see below |
| `--language` | `en` | no auto-detect; must match the caller |

**Media edge** (`services/media-edge/server.py`)

| Flag | Default | Note |
| --- | --- | --- |
| `--stt` | `127.0.0.1:50051` | k8s DNS in the cluster |
| `--tts` | `http://127.0.0.1:8083` | nemo-speech serve base URL |
| `--llm-model` | `qwen/qwen3.8-27b` | Groq; `llama-3.3-70b-versatile` no longer exists |
| `--voice` | `Aria` | Aria, Jason, John, Leo, Sofia — no cloning |
| `--silence-ms` | `900` | endpoint patience; 600 commits mid-sentence |
| `--min-speech-ms` | `200` | also causes the onset clipping bug |
| `--vad-threshold` | `0.5` | |

### Testing without a browser

```bash
cd services/stt
../../.venv/bin/python tests/smoke_client.py          # streams a WAV at 20ms cadence
../../.venv/bin/python tests/test_state_isolation.py  # proves one model serves N calls
```

### Capacity

Measured on a 4 GB RTX 3050 with `small` at 0.5 s chunks, 10.6 s of audio:

| concurrent calls | wall time | verdict |
| --- | --- | --- |
| 1 | 10.93 s | keeps up |
| 2 | 11.19 s | keeps up |
| 3 | 13.86 s | **3.3 s behind, lag accumulates** |

Hence `--max-concurrent 2`. Re-measure for a different card or model rather than
trusting that number.

---

## Data stack: Kafka + Schema Registry + Kafka UI

Docker, ~1.2 GB RAM. Not needed by the voice loop. See `BIG-DATA.md` §8 for the topic design.

```bash
docker compose -f data/docker-compose.yml up -d --wait        # start, waits for health checks
data/kafka/topics.sh                                          # create/update topics (re-runnable)
.venv/bin/python data/kafka/register_schemas.py               # register events.proto
.venv/bin/python data/kafka/smoke.py                          # produce + consume one Event
```

Kafka UI: **http://localhost:8090**. After editing `events.proto`, run
`register_schemas.py --check` before deploying: the registry rejects breaking changes
(`FULL_TRANSITIVE`).

```bash
docker compose -f data/docker-compose.yml down       # stop, keep data
docker compose -f data/docker-compose.yml down -v    # stop and wipe topics + schemas
```

---

## Stopping

Stop in reverse order — edge first so no call is mid-turn, then the GPU services.
`SIGTERM` (not `-9`): STT runs a drain handler and logs `draining… → stopped`.

```bash
# stop one service by the port it holds
stop_port() {
  PID=$(ss -ltnp 2>/dev/null | grep ":$1 " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
  [ -n "$PID" ] && kill -TERM "$PID" && echo "stopped :$1 (pid $PID)" || echo ":$1 already down"
}

stop_port 8082     # media edge
stop_port 50051    # STT
stop_port 8083     # TTS
```

Verify:

```bash
for p in 8082 50051 8083; do
  ss -ltn | grep -q ":$p " && echo ":$p STILL UP" || echo ":$p stopped"
done
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader   # expect ~0 MiB
```

**A released port does not mean the process exited.** gRPC pollers and the CUDA
runtime leave non-daemon threads behind, so a service can log `stopped`, free its
port, and still sit there holding ~1.8 GiB of VRAM. `services/stt/app/server.py`
now calls `os._exit(0)` after the drain for exactly this reason. If VRAM does not
return to ~0, check for survivors:

```bash
nvidia-smi --query-compute-apps=pid,used_memory,process_name --format=csv
kill -9 <pid>
```

> **Do not use `pkill -f` here.** `pkill -f "media-edge/server.py"` matches *your own
> shell command*, which contains that string, and kills the terminal you typed it in.
> This cost real time more than once. The bracket trick (`pkill -f "server[.]py"`)
> only helps when the pattern itself is the sole match — if your command line also
> contains the literal target string, it still self-matches. **Kill by port, as above.**

Stopping the GPU services frees their VRAM (STT ~1.4 GiB, TTS ~1.0 GiB). Leaving them
up between tests is fine and saves the ~3 s warm-up, but they hold ~2.8 GiB of the
4 GiB card, which matters if you want the GPU for anything else.

---

## Spikes

Throwaway experiments, kept for the measurements in their READMEs.

```bash
# live transcription, self-contained (own model in-process, no gRPC)
cd spikes/live-transcribe
../../.venv/bin/python server.py --model-path ~/.cache/whisper/small.pt --segment-length 0.5
# -> http://localhost:8081

# WebRTC audio measurement — headless, prints a report and exits
cd spikes/webrtc-audio
../../.venv/bin/python drive_headless.py --frames 500

# Simul-Whisper as a streaming transcriber, from a file
cd spikes/simul-whisper
../../.venv/bin/python run_stream.py --audio espeak_16k1.wav --truth truth1.txt

# offline resample benchmark
cd spikes/webrtc-audio && ../../.venv/bin/python bench_resample.py
```

---

## Old pipeline (reference only)

`voice-pipeline/` is the inherited Azure-based stack. It is **not wired up** and
its STT/TTS need Azure keys. Kept for reference; see `TECHNICAL-TIPS.md` §6 for the
bugs found in its orchestrator.

```bash
cd voice-pipeline && cp .env.example .env   # needs SPEECH_KEY / GROQ_API_KEY
docker compose up --build
```

---

## Troubleshooting

**Port already bound.** Nothing here handles `EADDRINUSE` gracefully:

```bash
PID=$(ss -ltnp | grep ":50051" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2); kill $PID
```

**`RESOURCE_EXHAUSTED: at capacity`** — STT is at `--max-concurrent`. Expected; the
orchestrator should route to another worker. Close an old browser tab.

**UI shows `STT error UNAVAILABLE`** — the STT service isn't running, or `--stt`
points at the wrong target.

**First call is slow** — you connected before warm-up finished. Wait for the log line.

**GPU out of memory** — something else is holding VRAM. `nvidia-smi` to check; the
desktop compositor typically takes ~690 MiB, and `small` needs ~1.4 GiB.
