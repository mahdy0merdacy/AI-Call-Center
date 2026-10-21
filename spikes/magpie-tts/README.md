# Spike: NVIDIA Magpie TTS Multilingual

**Question:** can we run Magpie TTS locally, does it sound right, and is it fast
enough for a live call?

Answers: yes, yes, and **not on CPU**.

## What it is

`nvidia/magpie_tts_multilingual_357m` — 364M params, 427 MiB as F16 GGUF, plus a
75 MiB NanoCodec decoder. NVIDIA Open Model License, **not gated**, commercial use
allowed. Outputs 22.05 kHz mono PCM16, resampleable on the way out.

Twelve languages: en-US, es-US, fr-FR, de-DE, zh-CN, vi-VN, it-IT, hi-IN, ja-JP,
ko-KR, ar-AR, pt-BR.

**Five fixed speakers** — Aria, Jason, John, Leo, Sofia. Zero-shot voice cloning was
deliberately removed by NVIDIA for security reasons, so a custom brand voice is not
possible with this model.

## How it's installed

Not via the NeMo Python toolkit — that pulls a large dependency tree and would fight
with the torch 2.14+cu130 our STT depends on. Instead NVIDIA's own C++ runtime,
which uses ggml and touches nothing in `.venv`:

```bash
curl -fsSL https://github.com/NVIDIA/NeMo-Speech.cpp/raw/main/scripts/install.sh -o install.sh
sh install.sh --no-modify-path --prefix "$HOME/.local/nemo-speech"
~/.local/nemo-speech/bin/nemo-speech pull magpie      # 523 MiB
```

Binary at `~/.local/nemo-speech/bin/nemo-speech`, models under
`~/.cache/nemo-speech/models/`.

> The NIM microservice route wants **16+ GB VRAM** and Compute Capability ≥ 8.0.
> The GGUF runtime does not — this machine's 4 GB card is fine.

## Verified

Synthesis was checked by **round-tripping through our own STT** rather than by
listening — synthesize known text, transcribe it, compare.

| input | STT heard back |
| --- | --- |
| "Zebra, parliament, quantum, seventeen, marmalade, thunderstorm, oblique." | "Zebra, Parliament, Quantum, Seventeen, Marmalade, Thunderstorm, **Obleek**." |
| "Your account balance is four hundred and twenty euros." | "Your account balance is 420 euros." ✓ |
| "Bonjour, merci d'avoir appelé notre service client." (fr-FR) | exact ✓ |

The only miss is `oblique` → `Obleek`, which is the STT spelling a correctly
pronounced rare word, not a TTS error. **French came back character-perfect.**

Voices Aria, Jason and Sofia all render and differ in length for the same text.

## Speed

| | CPU (Ryzen 5 5600H) | **GPU (RTX 3050)** |
| --- | --- | --- |
| "Thank you for calling…" | 0.77× realtime | **2.27×** |
| Longer hold message | 0.82× realtime | **2.31×** |
| Effective RTF | ~1.30 (slower than realtime) | **~0.45** |

CPU is unusable: 0.8× realtime means it produces 0.8 s of speech per second of
compute, so streaming playback underruns immediately and never recovers. **The CUDA
build is mandatory.**

On GPU: **peak VRAM 1036 MiB**, model load ~1.0 s per process, and RTF ~0.45 holds
steady across utterance lengths — unlike Whisper, cost scales with text length rather
than being pinned to a fixed window.

Alongside STT (~1.4 GiB) that's ~2.4 GiB of the 4 GiB card, which fits but leaves
little room for the desktop's ~690 MiB. On the 5-node plan, TTS and STT can share a
GPU laptop; on this machine both at once is tight.

### It does not stream — this shapes the service

`/v1/audio/speech` measured TTFB 2.498 s against a total of 2.499 s. **The gap is
0.5 ms**: the whole WAV is synthesized, then sent. There is no progressive output.

| | time to first audio |
| --- | --- |
| whole 134-char reply in one request | **3.25 s** |
| first clause only | **~0.6 s** |

So the TTS service must **split the reply into clauses, synthesize them in sequence,
and emit each as it completes.** Because RTF (0.45) is comfortably below 1, once
playback starts, synthesis of later clauses outpaces it — the buffer only grows.
Sending the whole reply in one request would put 3+ seconds of silence on top of STT
endpointing and LLM latency.

### Installing the CUDA build

The prebuilt artifact exists (`…-linux-x86_64-cuda.tar.gz`, 102 MiB), but GitHub's
CDN stalls the full-file GET from this network at 0 bytes while serving **ranged**
requests fine. `scripts/chunked_dl.sh` pulls 8 MiB ranges and concatenates.

There is no `cuda12`/`cuda13` artifact despite what the release notes imply — the
filename is plain `cuda`, and the installer's auto-detection resolves to it correctly.

Verify the result before installing: the archive must be exactly **107310946 bytes**
and pass `tar tzf`. A partially-overlapping download produced a 110 MiB file that
looked plausible and was corrupt.

## Useful flags

```bash
nemo-speech synthesize "text" \
  --output out.wav --force \
  --sample-rate 16000 \        # 8 kHz up to the model's 22.05 kHz
  --voice Aria \               # or --speaker N
  --language fr-FR \
  --format pcm \               # raw PCM16 instead of a WAV container
  --output - \                 # stdout, for piping into a service
  --device cuda                # once the CUDA build is in place
```

`nemo-speech serve` exposes an OpenAI-compatible HTTP API (`/v1/audio/speech`) plus
a browser playground and a realtime ASR WebSocket — a plausible shortcut to a TTS
service, though it gives less control than calling the binary or linking the C API.

## Verified on GPU too

Round-tripped through STT again after switching to CUDA — both **character-perfect**:

```
asked: Thank you for calling. How can I help you today?
heard: Thank you for calling. How can I help you today?

asked: Your call is important to us. Please hold while I look up your account details.
heard: Your call is important to us. Please hold while I look up your account details.
```

## For the service

- **Keep the model resident.** Each CLI invocation reloads ~500 MiB and costs ~1.0 s.
  Use `nemo-speech serve` (or the C API in `include/nemo_speech/tts.h`) rather than
  shelling out per utterance.
- **Chunk by clause**, per the streaming measurement above.
- `--sample-rate 16000` works directly, so no resampling is needed to match the STT
  side; WebRTC playback will still want 48 kHz.
- **No voice cloning.** Five fixed speakers only — a custom brand voice is not possible
  with this model.
