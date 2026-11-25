# Orchestrator

The control plane: one bidirectional gRPC stream per call with the media edge.
Events come up (transcripts, barge-in), directives go down (what to say, when to
listen longer, when to hang up). **No audio crosses this boundary.** Holds no
model; the LLM is any OpenAI-compatible endpoint.

```
media edge ──gRPC :50052──▶ orchestrator ──▶ LLM (LLM_URL)
                               │  ├──▶ MCP tool servers (MCP_SERVERS)
                               │  ├──▶ knowledge :8100 (KNOWLEDGE_URL)   platform clients' documents
                               │  └──▶ Postgres (PLATFORM_DATABASE_URL) platform clients, as voice_reader
                               └──▶ Kafka (KAFKA_BOOTSTRAP)             call events, optional
```

## Running

```bash
cd services/orchestrator && ../../.venv/bin/python -m app.server     # :50052
```

With nothing set it serves the test tenants (`tenants/*.yaml`) and calls the MCP
demo servers on 127.0.0.1:9101/9102, as in the local setup.

## Configuration

Environment only, so the same image runs under Compose and k3s. Each flag
(`--port`, `--llm-url`, ...) still exists and defaults to its variable.

| Variable | Default | |
|---|---|---|
| `HOST`, `PORT` | `0.0.0.0`, `50052` | where gRPC listens |
| `GROQ_API_KEY` | (none) | LLM key; a Secret in k8s |
| `LLM_URL` | Groq | any OpenAI-compatible chat completions URL |
| `LLM_MODEL` | `qwen/qwen3.8-27b` | |
| `TENANTS_DIR` | `tenants/` (`/srv/aicc/tenants` in the image) | test tenants |
| `MCP_SERVERS` | the demo servers on 127.0.0.1 | `tool=url,...`, e.g. `lookup_shipment=http://mcp-acme:9101/mcp,reschedule_delivery=http://mcp-acme:9101/mcp,check_line_status=http://mcp-swiftnet:9102/mcp` |
| `PLATFORM_DATABASE_URL` | unset: test tenants only | portal Postgres DSN for `voice_reader`; env only, never a flag |
| `KNOWLEDGE_URL` | unset: no retrieval | e.g. `http://knowledge:8100` |
| `SHUTDOWN_GRACE_S` | `25` | how long live calls get to finish after SIGTERM |
| `KAFKA_BOOTSTRAP`, `SCHEMA_REGISTRY_URL` | unset: no events | call events (`libs/aicc_events`) |

A malformed `MCP_SERVERS` or `KNOWLEDGE_URL` stops startup with an error, rather
than failing mid-call. Logs show where each dependency points, without credentials.

## Tenants

Resolved at call start, so a client who signs up in the portal is callable
without a restart:

1. **Test tenants**, `tenants/*.yaml`: flows, tools, knowledge in the prompt.
   They never depend on Postgres.
2. **Platform clients**, `accounts_tenant` where `is_active`: a generic assistant
   (en-US, voice Aria, no flows, no tools) that answers only from the client's
   documents. Retrieval is keyed by the client's UUID.

The YAML wins a slug collision (logged; the client is left out of `ListTenants`).
Postgres answers are cached 30 s (found) and 5 s (not found), and every lookup is
bounded at 2 s. If Postgres fails, the last answer is served (and not re-asked for
5 s); a slug never seen before is refused with `UNAVAILABLE`, not `NOT_FOUND`.
`voice_reader` may read four columns of `accounts_tenant` (`id, name, slug,
is_active`) and `document_chunks`; the queries name exactly those.

`ListTenants` feeds the test page's client picker: test tenants, then active
clients with their ingested document counts.

## Retrieval

For a platform client, every turn that calls the LLM first asks the knowledge
service (`POST /v1/search`, top 4, 1.5 s budget) for passages matching what the
caller said, and puts them in the prompt as the only material to answer from. No
match: the model is told the documents don't cover it. Search failed: it is told
the lookup is unavailable and must not guess. Each turn logs hits and latency:

```
[01J...] retrieval: 4 hit(s) in 85 ms (search 60 ms)
```

## Health and shutdown

- gRPC health (`grpc.health.v1`), service `""` and
  `aicallcenter.orchestrator.v1.Orchestrator`. In k8s use a native gRPC probe on
  port 50052. The image's Docker `HEALTHCHECK` runs `python -m app.healthcheck`.
- On SIGTERM: health goes `NOT_SERVING` at once (no new calls routed here), new
  calls are refused with `UNAVAILABLE`, live calls get `SHUTDOWN_GRACE_S` to end,
  then the server stops; calls still open are cut and recorded as errors. A
  second signal skips the wait.
- So the container needs more than 25 s to stop: `stop_grace_period: 35s` in
  Compose, `terminationGracePeriodSeconds: 35` in k8s. Docker's 10 s default
  would cut calls mid-sentence.
- Health reports `NOT_SERVING` for the whole drain, so do not use it as a
  liveness probe with less than ~30 s of tolerance (a TCP probe is fine).

## Tests

```bash
../../.venv/bin/python -m pytest -q && ../../.venv/bin/ruff check . && ../../.venv/bin/python -m mypy app tests
```

No Postgres, LLM or knowledge service needed: the drain tests run a real gRPC
server on a free localhost port, the rest use fakes.
