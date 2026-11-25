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

Then open **http://localhost:8082**, choose a client, click *Start mic*, talk.

Useful flags: `--silence-ms` (endpoint patience, default 900), `--min-speech-ms`,
`--vad-threshold`.

## Configuration

These flags default to environment variables, and an explicit flag still wins. Containers use the variables only, set to service names (the compose
network's DNS, Kubernetes Services in the cluster). An empty variable counts as
unset.

| Flag | Variable | Default |
|---|---|---|
| `--host` | `HOST` | `127.0.0.1` (the image passes `--host 0.0.0.0`) |
| `--port` | `PORT` | `8082` |
| `--stt` | `STT_ADDR` | `127.0.0.1:50051` |
| `--orchestrator` | `ORCHESTRATOR_ADDR` | `127.0.0.1:50052` |
| `--tts` | `TTS_URL` | `http://127.0.0.1:8083` |
| `--default-tenant` | `DEFAULT_TENANT` | `acme` (for an `/offer` that names no tenant) |

## Endpoints

| Route | What |
|---|---|
| `GET /`, `GET /w/<slug>` | the browser test page; `/w/<slug>` calls that client |
| `GET /tenants` | the client list from the orchestrator's `ListTenants` (2 s deadline): `{"tenants": [{"slug", "display_name", "locale", "source", "documents"}]}`, or `503 {"error"}` if the orchestrator cannot answer |
| `POST /offer` | WebRTC signalling: `{"sdp", "type", "tenant"}` |
| `GET /healthz` | liveness/readiness probe, with the active-call count |

The endpointing values are passed down to STT in the stream config — that's the
policy-pushed-down-as-parameters pattern: the edge (standing in for the
orchestrator here) owns the numbers, STT applies them.

## What the UI shows

The **client picker** lists the test tenants (`tenants/*.yaml`) and the platform's
active clients, with their ingested document count. Choosing one opens
`/w/<slug>` and keeps the query string, so `?autostart&stopafter=20` still works.
At `/` the page waits for that choice; the server's default tenant is only for API
callers. If the list cannot load, the page says so and a `/w/<slug>` link still
calls.

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

## Checks

```bash
../../.venv/bin/pip install -r requirements-dev.txt   # once, if the venv lacks them
../../.venv/bin/python -m pytest      # /tenants against an in-process fake orchestrator
../../.venv/bin/ruff check .
```

## Notes

- **`/tenants` opens a gRPC channel per request**, as each call does. The name is
  resolved afresh, so an orchestrator restarted at a new address answers the next
  page load; a long-lived channel would sit out its reconnect backoff first.
- **Bounded audio queue** (200 chunks). If STT stalls, frames are dropped rather
  than queued unboundedly — a hiccup should not become an OOM.
- **`RESOURCE_EXHAUSTED` is surfaced to the UI** rather than silently failing, so
  you can see when every STT slot is busy.
- No TLS — `insecure_channel` is fine on one machine, the cluster needs mTLS.
- The orchestrator does not exist yet. The edge currently sets the endpointing
  policy itself; that moves to the orchestrator when it's built.
