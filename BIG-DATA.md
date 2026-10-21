# Big data

The goal is to learn the data stack (Kafka, real-time pipelines, Postgres, Airflow, Databricks),
using the data this app actually produces, with jobs it would actually need.

Built step by step. Each step has a status in [§6 Roadmap](#6-roadmap). The reasoning behind
every decision is in [`DDIA.md`](DDIA.md).

---

## 1. Constraints

**Real calls will never be "big".** GPU STT allows **2 concurrent calls** on the 4 GB card
(`TECHNICAL-TIPS.md` §11). Volume has to come from a **synthetic call generator** that skips the GPU.
It sends text `Transcript` events straight into the orchestrator over gRPC, or produces directly to
Kafka. That same generator is also the orchestrator's load test.

**Nothing data-related goes on the hot path.** End of speech to first audio is ~2.5 s, and that
budget was hard-won. Producers are async and fire-and-forget, with a bounded buffer. If Kafka is
down, the call still works and events are dropped (and counted). A data stack that can slow a call
is a bug.

**Hardware budget (this laptop, re-measured 2026-10-13).** 12 cores, 15 GB RAM with ~7 GB available
while the desktop and IDE are open, and 247 GB of free disk on the same partition Docker uses.

- **RAM is the binding limit.** Run the voice loop *or* the heavy data stack, not both. The
  simulator path needs no GPU, so data work runs with STT and TTS stopped. Data-stack budget is
  ~4 GB: Kafka ×3 at 384 MB heap each, Postgres ×2, Schema Registry and Kafka UI. Spark runs in
  local mode, only when needed.
- **Disk is no longer tight, but every topic still gets a byte cap**, as well as a time cap. A
  runaway simulator should hit a limit, not fill the disk. Watch out: `retention.bytes` is **per
  partition**, so a 6-partition topic at 256 MB can grow to 1.5 GB.
- **What waits for the 5-node cluster.** A real network partition between machines, clock skew
  between hosts, and replication that survives a *disk* failure (RF=3 on one laptop is three
  copies on one disk). Everything else runs in containers on one laptop.

---

## 2. Target architecture

```
                      ┌──────────── Postgres (OLTP) ─────────────┐
                      │ shipments, lines, calls, turns, tenants  │
                      └───────┬───────────────────▲──────────────┘
                     Debezium │ CDC               │ JDBC sink (live aggregates)
                              ▼                   │
orchestrator ─┐                                   │
stt ──────────┼─► KAFKA (+ Schema Registry/proto) ┼─► Spark Structured Streaming / Flink
media-edge ───┤   call.events, stt.events,        │     → realtime metrics + alerts
mcp-servers ──┤   turn.metrics, tool.calls,       │
call-simulator┘   cdc.shipments                   └─► Kafka Connect S3 sink → MinIO
                                                         (bronze, Parquet/Delta)
                                                              │
                         AIRFLOW (batch orchestration) ───────┤
                           dbt silver/gold, data quality,     ▼
                           triggers Databricks jobs ──► DATABRICKS: Delta, Lakeflow
                                                          pipelines, Unity Catalog, MLflow
                                          Grafana / Superset for dashboards
```

---

## 3. Each tool and its job here

| Tool | Job in this project | What you learn |
| --- | --- | --- |
| **Postgres** | (a) Real operational DB: replaces `tenants/fixtures/*.json`, so the MCP servers query tables. Adds `calls` and `turns` tables. (b) Debezium CDC source. | Schema design, indexes, logical replication |
| **Kafka** | Event backbone. KRaft in Docker locally, **Strimzi** on k8s. Key = `call_id`, so each call's events stay ordered within a partition. | Partitioning, consumer groups, retention, exactly-once, Kafka Connect |
| **Schema Registry** | We already have `.proto` contracts. Use Protobuf as the Kafka message format. | Schema evolution, compatibility modes |
| **Debezium** | A shipment status change in Postgres becomes a `cdc.shipments` event. That could later trigger an outbound "your parcel is delayed" call. | Change data capture |
| **Spark Structured Streaming** (or **Flink**) | Real-time metrics: p50/p95 latency per stage per tenant, escalation rate over a sliding window, tool error-rate alerts (an MCP server is down), slot re-ask hotspots. | Windows, watermarks, stateful streaming. Spark carries over to Databricks directly. Flink is the streaming industry standard. |
| **MinIO + Delta Lake** | Local S3. Bronze tables written by a Kafka Connect S3 sink. | Lakehouse, medallion layers, table formats |
| **Airflow** | **Batch only.** Nightly dbt, data-quality checks (Great Expectations or Soda), per-tenant daily reports, STT WER regression over stored audio, triggering Databricks jobs. | DAGs, sensors, backfills, idempotency |
| **dbt** | Silver/gold models on Postgres, then on Databricks. | Analytics engineering, tests, lineage |
| **Databricks** | Medallion pipeline on Delta, Lakeflow Declarative Pipelines (formerly DLT), Unity Catalog, **MLflow** for an intent classifier trained on our transcripts. | The platform itself |
| **Grafana / Superset** | Live ops dashboard, plus tenant reports. | Dashboards over streaming and batch data |

### Use cases that justify the stack

- **Live ops**:
  - active calls;
  - latency per pipeline stage;
  - an alert when a tenant's escalation rate or a tool's error rate spikes.
- **Where callers get stuck**: which slot is re-asked most, and which utterances fail it. This
  feeds back into tenant YAML (prompts, `pattern`, `silence_ms`).
- **Intent classifier**: `route()` in `services/orchestrator/app/flow.py` is keyword overlap, and
  its own docstring calls that a known gap. Labelled transcripts from real and simulated calls feed
  an MLflow-tracked classifier, which goes back into the product.
- **STT quality drift**: WER on a stored audio set, run by Airflow, trended over model and config
  changes.
- **Cost per call**: LLM tokens per turn per tenant. This is not captured yet, see §5.

---

## 4. Databricks: what to watch for

- **Free Edition** is serverless-only, with quotas, and costs nothing. The **14-day trial** on your
  own cloud account has every feature, but it uses credits. Use Free Edition for daily learning and
  save the trial for features Free Edition lacks.
- **Databricks cannot reach Kafka on your laptop.** There are three options:
  1. Managed Kafka with a public endpoint (a Confluent Cloud trial).
  2. Airflow uploads Parquet batches to a Databricks Volume, and Auto Loader ingests them.
  3. Learn locally on **Spark + Delta Lake** (same APIs). Use Databricks only for platform-specific
     features (Unity Catalog, Lakeflow, MLflow).
- Recommended: (3) day to day, (2) to connect the two worlds.

---

## 5. Step 1: what data the app produces today

All three services already share **one ID per call**. The edge mints `"{slug}-{epoch_ms}"`
(`services/media-edge/server.py:273`) and passes it as the STT `session_id` and as the
orchestrator `call_id`. That ID is the join key and the Kafka partition key.

Status legend:
- **emitted**: already a structured event, either on a gRPC stream or the browser data channel.
- **logged**: only a log line, so it has to be parsed or re-emitted.
- **dropped**: computed, then thrown away.
- **missing**: not computed anywhere.

### 5.1 Orchestrator: `services/orchestrator/app/`

The richest source. Everything passes through `Session.on_event` (`server.py:63`), which is the hook
for a Kafka producer.

| Event | Fields | Where | Status | Per |
| --- | --- | --- | --- | --- |
| call opened | `call_id`, `tenant_slug`, `locale`, active call count | `server.py:124` | logged | call |
| call ended | `call_id`, end `reason` | `server.py:165` | logged | call |
| `flow` | `kind` (say / ready_for_tool / open / escalate), `intent`, filled `slots` | `agent.py:127` | emitted (`TurnInfo`) | turn |
| `set_silence_ms` | endpointing window chosen for the next slot | `agent.py:133` | emitted (`SetEndpointing`) | turn |
| `tool_result` | `tool`, `outcome`, **full `data` payload** | `agent.py:192` | emitted (`TurnInfo` drops `data`) | tool call |
| tool latency | `tool`, ms, timeout | `tools.py:69-79` | logged | tool call |
| tool rejected | not on allowlist, or no endpoint | `tools.py:61,66` | logged | rare |
| `token` | each LLM token | `agent.py:239` | emitted (in-process only) | ~30–60 per LLM turn |
| `turn_complete` | `total_s`, spoken `text` | `agent.py:166` | logged | turn |
| `escalated` | (none) | `agent.py:146` | emitted (`Hangup`) | call |
| barge-in | (none) | `server.py:160` | logged | rare |
| `error` | `detail` (200 chars) | `agent.py:159` | logged | rare |
| slot filled | slot name, value | `flow.py:283` | logged | turn |
| slot attempt failed | slot, attempt n / max, utterance | `flow.py:293` | logged | turn |
| route score | best flow and its overlap score (threshold 0.4) | `flow.py:207` | **dropped** | turn |
| caller utterance | final transcript text | `server.py:151` | received, not stored | turn |
| conversation | `history` (last 20 messages) | `agent.py` | in memory, lost at hangup | call |
| LLM TTFT and usage | time to first token, prompt and completion tokens | `agent.py:74` | **missing** | LLM turn |

### 5.2 STT: `services/stt/app/`

| Event | Fields | Where | Status | Per |
| --- | --- | --- | --- | --- |
| session opened | `session_id`, `language`, active count | `server.py:96` | logged | call |
| `Ready` | model, device, segment length | `server.py:99` | emitted | call |
| `SpeechStart` | `at_sample` | `session.py:76` | emitted | utterance |
| `Partial` | `text`, `delta` | `session.py:96` | emitted | ~1–2 per second of speech |
| partial inference time | `infer_ms` | `session.py:96` | **dropped** (in the event, not in the proto) | chunk |
| `SpeechEnd` | `at_sample`, `silence_ms` | `session.py:109` | emitted | utterance |
| `Final` | `text`, `start_sample`, `end_sample` | `session.py:114` | emitted | utterance |
| `Metrics` message | `infer_ms`, `backlog_chunks`, `active_sessions`, `audio_s` | `stt.proto:106` | **defined, never sent** | (none) |
| audio gap | samples missing | `server.py:113` | logged | rare |
| session summary | chunks, `infer_p50_ms`, `infer_max_ms`, `audio_s` | `session.py:127` | logged at close | call |

### 5.3 Media edge: `services/media-edge/server.py`

Sends to the browser on the data channel (`self.send`). The channel already carries a JSON event
stream, which could be teed to Kafka.

| Event | Fields | Status | Per |
| --- | --- | --- | --- |
| `speech_start` / `speech_end` / `partial` / `final` | text, `audio_s`, `silence_ms` | emitted (browser) | utterance |
| non-speech final ignored | text (e.g. `[MUSIC]`) | logged | rare |
| `first_audio_s` | final transcript → first TTS audio, seconds | emitted (browser) | turn |
| `speaking` | clause text, WAV byte count | emitted (browser) | clause |
| TTS failure | HTTP status / exception | logged | rare |
| audio queue full | dropped frame | logged | rare |
| WebRTC | connection state, frame count | logged / in memory | call |
| per-clause TTS latency | request → WAV | **missing** | clause |
| raw audio | 16 kHz PCM16 = **32 KB/s ≈ 115 MB per call-hour** | streamed to STT, not stored | continuous |

### 5.4 Static and reference data

| Data | Where | Becomes |
| --- | --- | --- |
| Tenants, flows, slots (pattern, max attempts, silence), tools, outcomes | `tenants/*.yaml` | Dimension tables: `dim_tenant`, `dim_flow`, `dim_slot`, `dim_tool` |
| Shipments | `tenants/fixtures/shipments.json` | Postgres `acme.shipments`, then Debezium CDC |
| Phone lines | `tenants/fixtures/lines.json` | Postgres `swiftnet.lines`, then Debezium CDC |
| Knowledge docs | `tenants/fixtures/knowledge/*.md` | Unstructured. Later chunked and embedded for retrieval. |

### 5.5 Gaps to fix before any of this reaches Kafka

1. **No wall-clock timestamps.** Durations use `perf_counter`, which can't be compared across
   processes. Every event needs `ts` in UTC epoch ms, plus `call_id`, `tenant`, `service`, and a
   per-call `seq`.
2. **Logged-only facts** (call open/close, slot attempts, tool latency, turn_complete, barge-in,
   errors) need to become structured events.
3. **Dropped data**: STT `infer_ms` and the `Metrics` message, and the route score.
4. **Missing data**: LLM time to first token and token usage. Groq returns usage at the end of the
   stream when asked. Also per-clause TTS latency.
5. **PII**:
   - Transcripts and slots contain shipment IDs and phone numbers.
   - `tool_result.data` contains names (`signed_by`).
   - Decide per field: keep, hash, or drop, *before* bronze. Hashing `phone_number` still
     allows joins.
6. **ID collisions**: `"{slug}-{epoch_ms}"` can collide under the simulator's load. Switch to a
   UUID / ULID (ULID sorts by time).

### 5.6 Scale estimate (per call, ~3 min, ~6 turns)

| Stream | Events per call | Size |
| --- | --- | --- |
| Control events (flow, tool, turn, endpointing) | ~60 | ~15 KB |
| STT partials and finals | ~150–250 | ~30 KB |
| LLM tokens (if kept) | ~250 | ~25 KB |
| Raw audio (if kept) | continuous | ~5.8 MB |

The simulator at 100k calls gives ~30M events / ~7 GB of event data, without audio. That is
enough for partitioning, windowing and compaction to matter.

---

## 6. Roadmap

Ordered by DDIA concept (§7), not by tool. Each step introduces one tool *because* the concept needs
it.

| # | Step | DDIA | Status |
| --- | --- | --- | --- |
| 1 | Data inventory: what the app produces (§5) | ch.1 describing load | **done** |
| 2 | Event schema in Protobuf + topic design (§8) | ch.4 encoding & evolution | **done** |
| 3 | The log: Kafka, first 1 broker, then 3-node KRaft; Schema Registry; Kafka UI | ch.5 replication, ch.9 consensus | next |
| 4 | Producers in orchestrator / STT / edge: at-least-once, `event_id`, close the §5.5 gaps | ch.8, ch.11 | |
| 5 | Call simulator: load without the GPU, latency percentiles | ch.1 | |
| 6 | Postgres as system of record; `reschedule_delivery` becomes a real write | ch.2, ch.7 transactions | |
| 7 | Stream processing: derived views, windows, joins, idempotent sinks | ch.11 | |
| 8 | Outbox + Debezium CDC: Postgres writes become events without dual writes | ch.11, ch.12 | |
| 9 | Postgres replica: replication lag, read-your-writes | ch.5 | |
| 10 | Lake + batch: Parquet/Delta on MinIO, DuckDB/Spark, Airflow, rebuild a view from the log | ch.3, ch.10, ch.12 | |
| 11 | Databricks: medallion port, Unity Catalog, MLflow intent classifier | ch.10 | |

---

## 7. DDIA chapter by chapter: where each concept lives here

*Designing Data-Intensive Applications* (Kleppmann). For each concept: the concrete exercise in
this app, and whether it fits this laptop.

| Ch | Concept | The exercise here | Fits laptop? |
| --- | --- | --- | --- |
| 1 | Describing load and performance | Load = calls/s × events per call (§5.6). p50/p95/p99 per stage. **Never average percentiles** across windows or instances: merge histograms (HdrHistogram / t-digest) instead. | yes |
| 2 | Data models | Relational (shipments, calls) vs document (tenant YAML, `result_json` as JSONB). The same tenant config as JSONB vs normalised dim tables. | yes |
| 3 | Storage engines; OLTP vs OLAP | B-tree (Postgres) vs append-only log (Kafka segments) vs LSM (RocksDB state in stream jobs). Run one analytics query on Postgres rows vs Parquet columns in DuckDB, and measure. | yes |
| 4 | Encoding and evolution | `events.proto`: field numbers, unknown-field preservation, `optional`, Schema Registry `FULL_TRANSITIVE`. Tested in `data/tests/test_events.py`. | **done** |
| 5 | Replication | Kafka RF=3, `acks=all`, `min.insync.replicas=2`: kill a broker mid-produce. Postgres primary + replica: a caller reschedules, then asks "when is it?", and the read hits a lagging replica. | yes (containers) |
| 6 | Partitioning | Key by `call_id` (even spread) vs by `tenant_id` (hot partition). Adding partitions changes key→partition and breaks per-key order. Postgres declarative partitioning of `turns` by month. | yes |
| 7 | Transactions | Two concurrent `reschedule_delivery` calls on the same parcel: lost update under READ COMMITTED, then fixed with `SELECT … FOR UPDATE` or SERIALIZABLE. The simulator provides the concurrency. | yes |
| 8 | Trouble with distributed systems | Clocks are already handled in the schema (wall vs monotonic vs sample clock). Timeouts (tool 3 s). Process pauses. A real network partition between laptops. | partition: cluster |
| 9 | Consistency and consensus | KRaft is Raft: kill the active controller and watch the election. A partition is total-order broadcast. Idempotent producer + transactions = exactly-once inside Kafka. | yes |
| 10 | Batch processing | Spark local: join events × tenant dims (broadcast vs sort-merge). Airflow DAGs whose output is overwritten per date partition, so a re-run fixes a bug instead of duplicating rows. | yes (Spark ~2 GB) |
| 11 | Stream processing | A call is a **session window**. Stream-table join (events × tenant dims). Stream-stream join (`SpeechEnded` ↔ `FirstAudioPlayed`). Checkpoints, exactly-once, sinks that upsert on `event_id`. CDC with Debezium. Event sourcing: rebuild call state from events. | yes |
| 12 | The future of data systems | The log is the source of truth, and everything else is a **derived view**. Drop a derived table and rebuild it by replaying from offset 0. The outbox pattern vs dual writes. End-to-end dedupe on `event_id`. | yes |

---

## 8. Step 2: event schema and topics

**Schema:** [`data/proto/aicallcenter/events/v1/events.proto`](data/proto/aicallcenter/events/v1/events.proto).
The header comment holds the evolution rules and the clock rules.

```bash
data/gen.sh                                   # -> data/gen/ (Python + events.desc)
.venv/bin/python data/tests/test_events.py    # compat, PII, size checks
```

Decisions:

- **One envelope (`Event`) with a `oneof` payload**, not one message type per topic. A call's events
  must stay in one partition, in order, to replay a call or run a session window. Per-type topics
  would scatter one call over many logs, each with its own order.
- **Facts in the past tense** (`ToolInvoked`, not `InvokeTool`). An event records something that
  happened. It is immutable and never a command.
- **`event_id` (ULID) for idempotence.** Producers retry, so delivery is at-least-once. Sinks dedupe
  on `event_id` to get effectively-once results.
- **`seq` per (call, service)**, not per call. A per-call counter shared across three services
  would need a sequencer on the call path (a consensus problem, ch.9). Order across services comes
  from the partition (arrival order) or from audio samples (true order).
- **PII is declared in the schema** with a custom field option `(pii)`. The bronze sink redacts by
  reading the descriptor. The test fails if any string field is unclassified.
- **Binary vs JSON:** 188 B vs 419 B for a `ToolInvoked` event (2.2×). Measured by the test.

**Topics:**

| Topic | Key | Partitions | Cleanup / retention | Holds |
| --- | --- | --- | --- | --- |
| `calls.events.v1` | `call_id` | 6 | delete, 7 d **or** 256 MB/partition | every payload except partials |
| `calls.partials.v1` | `call_id` | 6 | delete, 24 h or 64 MB/partition | `PartialTranscribed`: high volume, low value |
| `calls.summary.v1` | `call_id` | 6 | **compact** | latest state per call, derived by the stream job in step 7 |
| `dlq.calls.events.v1` | `call_id` | 1 | delete, 14 d | records a consumer could not process |
| `cdc.*` | primary key | Debezium | compact | step 8 |

- **Why 6 partitions:** a topic's partition count is its maximum consumer parallelism. 6 is enough
  for this laptop's 12 cores, and divides evenly across 1, 2, 3 or 6 consumers. It is hard to
  change later (see the ch.6 note on adding partitions), so it is chosen now.
- **Why partials have their own topic:** different retention, and most consumers never want them.
  The *schema* is the same (`Event`). The producer picks the topic by payload type.
- **All call topics use `message.timestamp.type=LogAppendTime`**, so the broker stamps arrival time.
  `broker time − occurred_at_ms` is then producer-to-broker lag, plus skew. With the default
  `CreateTime` it would be the producer's clock again (DDIA.md §5).
- **Raw audio never goes into Kafka.** At 32 KB/s it would dominate every partition. If we store it,
  it goes to object storage (MinIO), and the event carries the URI.
- **The compacted topic** keeps only the latest record per key. It is a table stored as a log, and
  how a stream job holds "current state of every call" in a form that can be rebuilt.
