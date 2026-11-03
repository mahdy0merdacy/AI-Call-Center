# Media edge

Terminates WebRTC, forwards PCM to the STT service over gRPC, streams events back
to the browser on the peer connection's data channel. **Holds no model and needs
no GPU** — this is the service for the GPU-less node.

```
browser ──WebRTC/Opus──▶ media edge ──gRPC──▶ STT service
   ▲                         │                    │
   └──── data channel ───────┴──── events ────────┘
```

## Running

Two terminals. STT first — the edge is useless without it.

```bash
# terminal 1 — the GPU service
cd ../stt && ../../.venv/bin/python app/server.py --port 50051

# terminal 2 — the edge
../../.venv/bin/python server.py --port 8082 --stt 127.0.0.1:50051
```

Then open **http://localhost:8082**, click *Start mic*, talk.

Useful flags: `--stt` (target, use k8s DNS in the cluster), `--language`,
`--silence-ms` (endpoint patience, default 900), `--min-speech-ms`, `--vad-threshold`.

The endpointing values are passed down to STT in the stream config — that's the
policy-pushed-down-as-parameters pattern: the edge (standing in for the
orchestrator here) owns the numbers, STT applies them.

## What the UI shows

**Committed** text accumulates, numbered per utterance — this is exactly what the
LLM would receive. The *partial* line below it is transient and italic. Keeping
them visually separate is the point: one drives the conversation, the other is
only there so the caller sees something happening.

Stats: audio sent, VAD state, utterance count, committed words, and time to first
partial.

## Verified

Headless Chrome, `small` on an RTX 3050, 13.7 s of speech:

```
speech_start @0.3s
speech_end after 608ms silence
FINAL: Thank you for calling customer support. My account number is 4729.
       I need to update my billing address please.
```

STT side: 19 chunks, infer p50 200.6 ms, max 255.9 ms against a 500 ms budget.

## Notes

- **Bounded audio queue** (200 chunks). If STT stalls, frames are dropped rather
  than queued unboundedly — a hiccup should not become an OOM.
- **`RESOURCE_EXHAUSTED` is surfaced to the UI** rather than silently failing, so
  you can see when every STT slot is busy.
- No TLS — `insecure_channel` is fine on one machine, the cluster needs mTLS.
- The orchestrator does not exist yet. The edge currently sets the endpointing
  policy itself; that moves to the orchestrator when it's built.
