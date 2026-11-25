# Call simulator

Generates call data for the lake: scripted callers driving the real orchestrator,
plus deliberately broken events for the quality checks to catch.

| File | What it does |
|---|---|
| `fake_llm.py` | OpenAI-compatible streaming LLM with realistic latency. Free, fast, repeatable: no Groq calls. |
| `simulate.py` | N concurrent scripted calls over gRPC, across all three tenants. Every call is `synthetic=true`, `channel="simulator"`. |
| `faults.py` | Small calls written straight to Kafka, each broken in one way: duplicate, seq gap, missing end, clock skew, unknown tenant, LLM outlier, identifiers in free text, garbage bytes. |

## Recipe

Needs the data stack (`docker compose -f data/docker-compose.yml --profile spark up -d`, then `data/setup.sh`).

```bash
# 1. MCP servers (tools) and the fake LLM
(cd services/mcp-servers && ../../.venv/bin/python acme_delivery.py --port 9101 &)
(cd services/mcp-servers && ../../.venv/bin/python swiftnet_isp.py --port 9102 &)
.venv/bin/python data/simulator/fake_llm.py --port 8099 &

# 2. The orchestrator, publishing call events, talking to the fake LLM
(cd services/orchestrator && KAFKA_BOOTSTRAP=localhost:9092 SCHEMA_REGISTRY_URL=http://localhost:8085 \
  ../../.venv/bin/python -m app.server --llm-model sim-llm \
  --llm-url http://127.0.0.1:8099/v1/chat/completions &)

# 3. Calls, then faults
.venv/bin/python data/simulator/simulate.py --calls 200 --concurrency 8
KAFKA_BOOTSTRAP=localhost:9092 SCHEMA_REGISTRY_URL=http://localhost:8085 \
  .venv/bin/python data/simulator/faults.py

# 4. Into the lake, then check it (or let the lake_pipeline DAG do both)
SPARK_REMOTE=sc://localhost:15002 .venv/bin/python data/spark/jobs/bronze_call_events.py
SPARK_REMOTE=sc://localhost:15002 .venv/bin/python data/spark/jobs/silver_calls.py
S3_SECRET_KEY=... .venv-quality/bin/python data/quality/run.py
```

`--seed N` repeats the same mix of calls. `fake_llm.py --error-rate 0.05` makes 5% of
LLM calls fail, to exercise the error events.

## What each fault should trigger

| Fault | Where it shows | Severity |
|---|---|---|
| `duplicate` | removed by the silver MERGE; bronze unique ratio dips | none (handled) |
| `seq_gap` | `silver_calls.seq_gaps` | warning |
| `missing_end` | `silver_calls.open_after_1h` | warning |
| `clock_skew` | `bronze.ingest_lag_ms`, `silver_calls.min_ingest_lag_ms` | warning |
| `unknown_tenant` | `tenant_id` not in `tenants/*.yaml` | warning |
| `llm_outlier` | `silver_turns.llm_ttft_ms` | warning |
| `identifier_text` | masked by silver; the critical PII rule confirms nothing survived | none (handled) |
| `garbage` | `bronze/call_events_quarantine`; `quarantine_rate` | none unless frequent |

Each becomes critical past its tolerance (see `data/quality/suites.py`), e.g. seq gaps
on more than 1% of calls.
