# DDIA in this system

Every data-platform decision in this repo, the concept from *Designing Data-Intensive
Applications* (Martin Kleppmann) behind it, and why that concept applies to a voice call center.

Companion to [`BIG-DATA.md`](BIG-DATA.md): that file is the plan and the inventory. This one is
the reasoning. Chapter numbers refer to DDIA. The schema discussed throughout is
[`data/proto/aicallcenter/events/v1/events.proto`](data/proto/aicallcenter/events/v1/events.proto).

Each section has the same shape:

- **The idea**: what DDIA says, briefly.
- **Why it applies here**: the concrete failure in *this* app without it.
- **Decision**: what we chose.
- **Cost**: what we gave up. Every decision here costs something. A section with no cost is
  hiding it.
- **Status**: done, planned (roadmap step), or a cluster-only exercise.

---

## 0. Why DDIA applies to a small system

DDIA calls an application *data-intensive* when data is its main challenge, through its amount,
its complexity, or how fast it changes, rather than CPU. It is tempting to say this app doesn't
qualify: two concurrent calls on a 4 GB GPU is not big data.

It qualifies anyway, because it has the *properties* DDIA is about, even without the volume:

| Property | Where it shows up here |
| --- | --- |
| **Multiple processes that fail independently** | Media edge, STT, orchestrator, TTS, MCP servers, and soon Kafka and Postgres. Any of them can die mid-call. |
| **Multiple machines** | Target deployment is 4 laptops + 1 desktop under k8s (`TECHNICAL-TIPS.md` §4). |
| **No shared clock** | Those five machines disagree on the time. |
| **A hard real-time budget** | ~2.5 s from end of speech to first audio (`TECHNICAL-TIPS.md` §8). Anything that adds latency to a turn is a regression the caller hears. |
| **Data outlives code** | Events written today are read by a classifier trained next year, by code that doesn't exist yet. |
| **Personal data** | Transcripts, phone numbers, shipment IDs, names. A French tenant puts it under GDPR. |
| **Mixed versions in production** | k8s rolling upgrades mean old and new versions of a service run side by side. |

Volume comes from the simulator (roadmap step 5). The concepts below are what make volume
manageable when it arrives. They also apply to two calls.

DDIA's three goals are a useful lens for everything below:

- **Reliability:** keeps working when things go wrong. Things going wrong is the normal case.
- **Scalability:** has a plan for growth, and can describe its load in numbers.
- **Maintainability:** can be changed without fear: operable, simple, evolvable.

---

## 1. Keep the data pipeline off the call path

*Ch.1 reliability (faults vs failures). Ch.11 what to do when producers outrun consumers.*

**The idea.** DDIA separates a *fault* (one component deviating from spec) from a *failure* (the
system as a whole stops serving users). A reliable system is designed so that faults do not become
failures. Ch.11 then asks a related question about messaging: when a producer outruns its consumer
or its broker, there are three options: drop messages, buffer them, or apply **backpressure**
(block the producer).

**Why it applies here.** We are about to add Kafka to three processes that serve live calls. The
orchestrator's `Session.on_event` (`services/orchestrator/app/server.py:63`) runs *inside* the turn
loop. The edge's audio loop runs 50 times a second. Consider each option if Kafka is slow or down:

- **Backpressure (block):** the turn waits for Kafka. A slow broker turns a 1.6 s reply into a 10 s
  reply, or no reply. An analytics outage has become a call-handling outage, which is the clearest
  fault-to-failure escalation possible.
- **Unbounded buffer:** the call survives, but memory grows for as long as Kafka is down, until the
  OOM killer ends *every* call on that pod. This is the same reason the edge's audio queue is
  bounded at 200 (`services/media-edge/server.py:63`) and drops frames rather than growing.
- **Drop:** the call is untouched. We lose some analytics.

**Decision.** Producers are asynchronous with a **bounded** in-memory buffer. When it is full,
events are **dropped and counted**. The drop counter is itself a metric, so loss is visible
instead of silent. The call path never awaits Kafka.

**Cost.** The log is not a complete record. During a Kafka outage there are gaps, and every
downstream number for that window is an undercount. The schema makes the gaps *detectable*
(`seq`, §6), but it cannot fill them. For a call center's analytics this is the right trade: a lost
`TurnCompleted` event costs nothing, and a dead call costs a customer. It would be the *wrong* trade
for billing events. If we ever bill per call, those events take a different path (the outbox, §13).

**Status.** Planned, step 4.

---

## 2. Describing load and performance in numbers

*Ch.1 describing load, describing performance, percentiles, tail latency amplification.*

**The idea.** "Scalable" means nothing until you name the *load parameters*, the numbers that
growth is measured in. Performance is a **distribution**, not one number. Report percentiles
(p50, p95, p99), because the mean hides the slow requests users actually notice. Two traps:

1. **Percentiles cannot be averaged.** The mean of two instances' p95 is not the p95 of their
   combined traffic. To combine, merge the underlying histograms (HdrHistogram, t-digest).
2. **Tail latency amplification.** When one user-facing request waits on several backend calls,
   it is as slow as the slowest of them. So the user sees the backends' *tails* far more often
   than the backends' own p99 suggests.

**Why it applies here.**

- **Load parameters** for this system are calls per second, turns per call, and events per turn
  (BIG-DATA.md §5.6), plus concurrent calls per GPU, which is the real ceiling (2 on 4 GB).
- **A turn is a chain**: endpoint timer → STT final → (tool) → LLM first token → TTS first clause.
  Each stage has its own distribution. A turn that hits the LLM's p99 *and* a slow tool is the
  call the customer complains about. Tool calls already have a 3 s timeout
  (`services/orchestrator/app/tools.py:31`) for exactly this reason: they bound one stage's tail.
- The number we care most about, `FirstAudioPlayed.final_to_first_audio_ms`, is measured today
  only as a log line at the edge, for one call at a time.

**Decision.** Every stage gets its own duration field in its own event: `infer_ms`, `latency_ms`,
`ttft_ms`, `tts_ms`, `final_to_first_audio_ms`. Stream jobs keep **histograms** per window and per
tenant, and merge them, rather than storing per-instance percentiles.

**Cost.** Histograms take more state than a running mean, and a dashboard is harder to build from
them. This matters little at our scale.

**Status.** Fields done (step 2). Computation planned (steps 5 and 7).

---

## 3. Encoding and evolution: data outlives code

*Ch.4 formats for encoding data, schema evolution, modes of dataflow.*

**The idea.** Data moves between processes in three ways: through databases, through service calls,
and through asynchronous messages. In all three, the writer and the reader are often *different
versions* of the code. Two properties are needed:

- **Backward compatibility:** new code can read data written by old code.
- **Forward compatibility:** old code can read data written by new code. This is the harder one.

DDIA compares JSON, Thrift, Protocol Buffers and Avro. With Protobuf, the **field number** (tag)
*is* the field on the wire, and names are not sent at all. Evolution works by adding fields with
new numbers. Old readers skip numbers they don't know, and new readers see defaults for numbers
the writer didn't send.

DDIA's line here: *data outlives code*. You deploy new code in minutes, but the data written by
old code stays, often for years.

**Why it applies here, three times over.**

1. **Replay.** The point of a log (§13) is that a consumer written next year can re-read events
   from today. That is backward compatibility over an unlimited period.
2. **Consumers upgrade last.** We will change producers (the orchestrator) more often than
   consumers (a stream job, a dbt model). Between the two deploys, old consumers read new events.
   That is forward compatibility, needed on every deploy.
3. **Rolling upgrades on k8s.** During a rollout, pods of version N and N+1 run side by side and
   write to the same topic. The *existing* gRPC contracts (`stt.proto`, `orchestrator.proto`) have
   the same problem: an old edge talks to a new orchestrator for the length of the rollout.

**Decisions** (all in the header of `events.proto`):

- **Protobuf, not JSON.** A schema is enforced, tags make evolution mechanical, and the encoding
  is 2.2× smaller (measured: 188 B vs 419 B for one `ToolInvoked`). We already use Protobuf for
  gRPC, so there are no new tools.
- **Frozen numbers and names.** Never change or reuse a field number. Deleted fields become
  `reserved`. Names are frozen too, even though the wire ignores them, because JSON output and
  Spark column names use them.
- **Zero means "not reported".** proto3 has no `required`. Every consumer treats `0` / `""` as
  absent. Where zero is a legitimate value (`route_score` can be 0.0), the field is
  `optional` so presence is tracked.
- **Unknown fields survive.** Protobuf keeps fields it doesn't recognise and writes them back out
  on re-serialisation. A pass-through stage (redact, enrich, re-key) running old code will not
  silently delete fields a downstream consumer already depends on. DDIA warns about exactly this
  failure for databases. Proto handles it for us, and the test proves it.
- **A breaking change is a new package and a new topic** (`v2`), migrated by a stream job. It is
  never an edit in place.
- **Schema Registry with `FULL_TRANSITIVE` compatibility** (step 3). Every new schema version must
  be backward *and* forward compatible with *every* previous version, not just the latest. The
  rules stop being a convention: the registry rejects the deploy.

**Cost.** Protobuf is not human-readable in Kafka without tooling. Kafka UI needs the registry to
decode it. Schema changes become a process. The "zero means absent" rule is easy to forget in a
consumer: a dashboard that averages `ttft_ms` including zeros from events that predate the field
will report a better number than reality.

**Status.** Done (step 2). Proved by `data/tests/test_events.py`: an old reader with new fields, a
new reader with old records, an unknown payload type, and the size comparison. The registry comes
in step 3.

---

## 4. Events are facts: one envelope, past tense

*Ch.11 event sourcing, state vs streams, immutability. Ch.12 the log as the system of record.*

**The idea.** An **event** records something that *happened*. It is immutable, because the past
does not change. A **command** is a request that may still be refused. DDIA's view of state:
*current state is the result of applying a stream of events*, and the two are two
representations of the same information. Mutable state is derived, and the immutable log of facts
is the more fundamental of the two.

**Why it applies here.**

- The orchestrator already *decides* things that are facts the moment they happen: a turn was
  routed, a slot was rejected, a tool returned `delayed`, the call escalated. Today those facts live
  in log lines, then vanish (BIG-DATA.md §5).
- Every question we want to answer is a fold over a call's facts in order: "where do callers get
  stuck", "what was the latency of each stage", "did this call escalate after a tool error". That
  only works if all of a call's facts are **in one ordered sequence**.

**Decisions.**

- **Names in the past tense**: `ToolInvoked`, `SlotRejected`, `CallEnded`. Never `InvokeTool`. A
  consumer cannot refuse a fact, and the name should make that impossible to misread.
- **One envelope (`Event`) with a `oneof` payload**, and one topic for all of a call's events
  (`calls.events.v1`). With one topic per event type, a call's history would be scattered over many
  logs, each with its own order, and reassembling one call would become a multi-way join.
- **The envelope carries what every consumer needs** (`event_id`, `call_id`, `tenant_id`, `source`,
  `seq`, `occurred_at_ms`), so no consumer has to look into the payload to route, dedupe, or order.

**An honest limit.** This is **not** event sourcing of the orchestrator. A live call's state (the
slot pointer, the conversation history) lives in memory in `Session`, and that memory is the
source of truth while the call runs. The event log is the source of truth for **everything
downstream**: analytics, metrics, training data. Making the orchestrator rebuild a live call from
its events (to survive a pod restart mid-call) would be true event sourcing. It is a possible later
exercise, and it would put Kafka *on* the call path, which §1 forbids today.

**Cost.** One topic with one schema means a consumer that only wants `ToolInvoked` still reads and
discards everything else. At ~300 events per call that is negligible.

**Status.** Done (step 2).

---

## 5. Time: three clocks, three jobs

*Ch.8 unreliable clocks: time-of-day vs monotonic, clock skew, ordering by timestamp.*

**The idea.** A computer has two kinds of clock:

- **Time-of-day clock** (`time.time()`): synchronised by NTP. It can be **stepped backwards** when
  NTP corrects it, drifts between syncs, and differs between machines by milliseconds to seconds.
  It is good for "roughly when", and dangerous for "which came first".
- **Monotonic clock** (`time.perf_counter()`, `time.monotonic()`): only goes forward, which makes
  it right for measuring durations. But it is meaningful *only within one process*. Comparing
  two machines' monotonic values is meaningless.

DDIA's key warning is that **ordering events by timestamps from different machines is unsafe**.
With skew, an effect can carry an earlier timestamp than its cause. Last-write-wins on such
timestamps silently loses data.

**Why it applies here.** Our services will run on four laptops and a desktop, none with a reliable
clock. Two tempting mistakes:

- *"Latency = orchestrator's `Say` timestamp − STT's `Final` timestamp."* These come from two
  machines, so skew is added straight into the measurement. With 40 ms of skew, a 200 ms stage
  reads as 160 or 240 ms, or as negative if the skew is big enough.
- *"Sort the call by `occurred_at_ms`."* `UtteranceTranscribed` (edge machine) and `TurnDecided`
  (orchestrator machine) can appear in the wrong order.

**Decisions.** Three clocks, each used only for the job it can do:

| Clock | Field | Use it for | Never for |
| --- | --- | --- | --- |
| Wall clock, per producer | `occurred_at_ms` | "which hour / day", window bucketing | ordering across services |
| Monotonic, one process | every `*_ms` duration | latency of a stage | subtracting across services |
| **Audio sample position** | `at_sample`, `start_sample`, `end_sample` | true order of speech events within a call | (nothing: it's exact) |

The third is the useful one. Every service sees the *same* audio stream, so "sample 48 000" (3.0 s
into the call at 16 kHz) means the same instant everywhere, with no clock involved. It is a
**logical clock** that the domain provides for free. "Did the barge-in happen before or after the
caller finished the slot value?" is answered by comparing samples, not timestamps.

There is also a fourth timestamp: Kafka's record timestamp. With the topic setting
`message.timestamp.type=LogAppendTime`, the broker stamps each record on arrival. Then
`broker time − occurred_at_ms` measures producer-to-broker lag (plus skew). With the default
`CreateTime` it is just the producer's clock again, so we set `LogAppendTime` on our topics.

**Cost.** Consumers must know which clock to use for what. The schema comments say it, but
nothing enforces it. Durations measured in-process exclude the time *between* processes (network,
queueing). That gap is only visible as end-to-end numbers measured by one process, such as
`final_to_first_audio_ms`, which the edge measures on its own monotonic clock.

**Status.** Schema done (step 2). The skew exercise across real machines needs the cluster.

---

## 6. Ordering without a global sequencer

*Ch.9 ordering guarantees, causality, total order broadcast, sequence number ordering. Ch.11 logs.*

**The idea.** A **total order** puts every event in one agreed sequence. DDIA shows that producing
one across multiple nodes amounts to **consensus**: equivalent to total order broadcast, and it
needs a single leader or a consensus protocol. Lamport timestamps provide a total order consistent
with causality, but you only know it after the fact. A **log partition** is a total order, but only
of what *it* received, in arrival order. Arrival order is not causal order when producers race.

**Why it applies here.** A call's events come from three producers: the edge (speech and playback
events), the orchestrator (dialogue), and later the MCP servers. We'd like one `seq` per call
running 1, 2, 3… across all of them. To get that, someone must hand out numbers: a sequencer
service, or a counter in a database. Every event of every call would then wait on a network round
trip to that one component, which puts latency and a single point of failure on the call path
(§1).

**Decisions.**

- **`seq` is per (call, producing service)**, starting at 1, with no gaps. Each service has its
  own counter, so no coordination is needed. Within one producer, order is exact, a gap means a
  lost event, and a duplicate means a retry (§7).
- **Kafka key = `call_id`**, so all of a call's events land in one partition, where the broker
  puts them in one arrival order. Across producers, that order is *usually* right and is good
  enough for counting and windowing.
- **Where true cross-service order matters, use audio samples** (§5). The domain already has a
  shared clock for the events that need ordering, so we don't need to build one.

**Cost.** No consumer can answer "what was the exact global order of everything in this call" with
certainty. In practice the one question that needs it ("did the agent start speaking before the
caller finished?") is answered by samples.

**Status.** Done (step 2).

---

## 7. Delivery semantics: at-least-once plus dedupe

*Ch.11 fault tolerance, idempotence, exactly-once. Ch.12 the end-to-end argument.*

**The idea.** A producer that sends a message and gets no acknowledgement can't tell whether it was
lost or only the ack was. Retrying gives **at-least-once** delivery, which means duplicates. Not
retrying gives **at-most-once**, which means loss. "Exactly-once" is really *effectively-once*:
at-least-once delivery plus **idempotent** processing, so duplicates have no effect.

DDIA's **end-to-end argument** (ch.12): a guarantee given by an intermediate layer (TCP, the broker)
does not remove the need for a check at the *ends*. Only the endpoints know what a duplicate means
to the application.

**Why it applies here.** Kafka's idempotent producer (on by default since Kafka 3.0) already
dedupes retries. It is *not* enough, because:

- It dedupes per producer **session**, per partition. When a pod restarts, its new session can
  resend events the old session already wrote. In k8s, pods restart routinely.
- Consumers have the same problem from the other side. A stream job that crashes after writing to
  Postgres but before committing its Kafka offset will reprocess those events after restart.
- Duplicates here inflate counts that drive decisions: escalation rate, tool error rate. A spike of
  replayed `Escalated` events looks exactly like an incident.

**Decisions.**

- **Every event has an `event_id`** (ULID), assigned once by the producer when the event is
  created, before any retry. It goes in the envelope, so it is the same on every retry, from any
  session.
- **Sinks are idempotent**: Postgres writes are `INSERT … ON CONFLICT (event_id) DO NOTHING` (or an
  upsert). Lake writes are deduplicated on `event_id` in silver.
- **ULID rather than UUIDv4**, because ULIDs sort by creation time. That keeps index inserts at the
  end of a B-tree (cheaper, §14) and makes IDs roughly time-ordered for debugging. It is not used
  for ordering logic: it carries a wall-clock component (§5).
- Kafka's idempotent producer stays on. It is cheap and removes the common case. `event_id` covers
  the cases it misses.

**Cost.** Every sink needs a unique index on `event_id`, which takes write throughput and storage.
Dedupe in a stream job needs state (seen IDs per window), and that state has to be bounded by time
or it grows forever.

**Status.** Schema done (step 2). Producers in step 4, sinks in step 7.

---

## 8. Partitioning: by call, not by tenant

*Ch.6 partitioning by key hash, skew and hot spots, rebalancing.*

**The idea.** Partitioning spreads data and load over several nodes. Partitioning by **hash of a
key** spreads load evenly but loses range locality. **Skew** happens when one key gets far more
traffic than others (a "hot spot"), so one partition does the work of many. **Rebalancing** moves
data when nodes or partitions are added. DDIA warns against `hash mod N`: when N changes, almost
every key moves.

**Why it applies here.**

- **Candidate keys** are `call_id`, `tenant_id`, or none.
  - `tenant_id` sounds natural ("all of Acme's data together"), but tenants differ in size by
    orders of magnitude. If Acme is 90% of traffic, one partition does 90% of the work, and adding
    partitions does nothing for it.
  - `call_id` is high-cardinality and evenly spread, and it keeps a call's events together, which
    is the property §4 needs.
  - No key at all spreads evenly, but scatters each call over all partitions.
- **Kafka's default partitioner is `hash(key) mod partitions`.** This is the case DDIA warns about.
  Kafka *can* add partitions to a topic, but keys then map to different partitions. A call that was
  in progress during the change has its events split across two partitions, with no ordering
  between them.

**Decisions.**

- **Key = `call_id`** on all call-scoped topics.
- **6 partitions, chosen now.** A topic's partition count is its maximum consumer parallelism. 6
  divides evenly across 1, 2, 3 or 6 consumers, and is plenty for a 12-core laptop and a 5-node
  cluster. We choose the number up front because changing it later is the dangerous operation
  above.
- **Postgres** will partition `turns` / `events` tables **by time range** (monthly). That is range
  partitioning on purpose: queries are almost always "last N days", and dropping an old month is a
  metadata operation, not a giant `DELETE`.

**Cost.** Per-tenant queries must read all partitions. Fine for streaming aggregates. For batch,
the lake is partitioned by date and tenant instead (§15), because that layout serves batch reads.

**Status.** Decided (step 2). Topics created in step 3. The skew exercise (re-keying by tenant
with a skewed simulator) is planned for step 5.

---

## 9. What goes in the log, and for how long

*Ch.3 log-structured storage, compaction. Ch.11 log-based brokers, disk space, log compaction.*

**The idea.** A Kafka partition is an append-only log split into **segments** on disk. Old segments
are deleted by retention (time or size), or **compacted**: only the latest record per key is
kept, so the log becomes a table. Ch.11 notes that a log broker's buffer is bounded by disk.
That is generous, but finite.

**Why it applies here.** Our streams differ wildly in volume and value:

| Stream | Volume | Value |
| --- | --- | --- |
| Dialogue / lifecycle events | ~60 per call | high: every analysis needs them |
| STT partials | ~200 per call | low: latency analysis only, content is unstable by definition |
| Raw audio | 32 KB/s ≈ 115 MB per call-hour | high for WER evaluation, but huge |

Putting them in one topic would make the retention of everything depend on the noisiest stream.

**Decisions.**

- **`calls.events.v1`**: delete policy, 7 days, plus a byte cap.
- **`calls.partials.v1`**: same `Event` schema, its own topic, 24 h. The producer chooses the
  topic by payload type. Most consumers never subscribe.
- **Raw audio never goes into Kafka.** Large blobs go to object storage (MinIO). If we store audio,
  the event carries the object's URI, and Kafka carries a pointer, not the payload. This is the
  standard pattern for large values.
- **`calls.summary.v1`** is **compacted**, keyed by `call_id`: a stream job writes the latest
  summary of each call, and compaction keeps only the newest. The topic is a *table stored as a
  log*. Any consumer can rebuild "current state of every call" by reading it from the start, which
  is the stream/table duality.
- **Byte caps on every topic**, as well as time limits. `retention.bytes` is **per partition**, so
  the topic total is `partitions × retention.bytes`.

**Cost.** Seven days of raw events means a stream job that must be rebuilt *from scratch* can only
replay a week. Older history has to come from the lake. That is fine by design: Kafka is the
short-term log, and the lake is the long-term record (§15).

**Status.** Decided (step 2). Applied as topic configs in step 3.

---

## 10. Replication

*Ch.5 leaders and followers, synchronous vs asynchronous, replication lag, read-your-writes.*

**The idea.** Replication keeps copies of data on several nodes, for fault tolerance and read
scaling. In leader-follower replication, writes go to a leader, and followers copy them. **Synchronous**
followers are durable but slow; **asynchronous** ones are fast but can lose recent writes on
failover. Followers that lag give stale reads. DDIA describes the anomalies one by one, the most
important being **read-your-writes**: a user who just wrote must see their own write.

**Why it applies here.** In two places:

1. **Kafka.** On one broker, a disk failure loses the log. With replication factor 3:
   - `acks=all` means the leader acknowledges only after all **in-sync replicas** have the record.
   - `min.insync.replicas=2` means that if fewer than 2 replicas are in sync, the leader *refuses*
     writes rather than accepting them with no redundancy.
   - Together, an acknowledged event survives the loss of any one broker. That is a deliberate
     choice of consistency over availability for writes.
2. **Postgres (step 9).** The concrete read-your-writes failure is this:
   - A caller says "move my delivery to Friday". `reschedule_delivery` writes to the primary.
   - Next turn, the caller asks "so when is it coming?". `lookup_shipment` reads from a replica
     that hasn't applied the write yet, and the agent says "Wednesday".
   - The caller is told the reschedule didn't happen, in a conversation with a single participant
     and two turns.
   - The fixes DDIA lists all apply here:
     - read from the leader for anything the caller may have just changed;
     - pin a call to the primary for N seconds after any write;
     - or track the write's log position (LSN) and wait until the replica has reached it.

**Decision.** Kafka at RF=3, `acks=all`, `min.insync.replicas=2`. Postgres primary + one async
replica. Tool reads go to the **primary** for the rest of a call after that call has written.

**Cost, and an honest caveat.** RF=3 *on one laptop* is three copies on the same disk. It protects
against a broker process dying, not against the disk dying. Locally it teaches the **mechanics**
(ISR shrinking, leader election, refused writes). Real fault tolerance arrives with the 5-node
cluster. RF=3 also triples write I/O and disk use.

**Status.** Planned: Kafka in step 3, Postgres replica in step 9.

---

## 11. Consensus: KRaft is Raft

*Ch.9 consensus, leader election, quorums, fencing tokens and epochs.*

**The idea.** Nodes have to agree on things: who the leader is, and which writes are committed.
Raft-style consensus algorithms need a **majority quorum**. With `n` voters, `⌊n/2⌋ + 1` must agree,
so 3 nodes tolerate 1 failure and 5 tolerate 2. **2 nodes tolerate 0**, which is no better than 1
and adds a second thing that can fail. A leader that was replaced but hasn't noticed (a "zombie",
for example after a GC pause) must be **fenced**: every leadership term gets an increasing
**epoch number**, and anything stamped with an old epoch is rejected.

**Why it applies here.** Kafka no longer uses ZooKeeper. Its metadata (topics, partition leaders,
ISR membership) is managed by a **KRaft controller quorum**, which is a Raft implementation. And
the patterns appear one level up as well:

- **Partition leader epochs**: a follower that comes back after being cut off truncates its log to
  match the new leader's epoch. Fencing at the log level.
- **Producer epochs**: a transactional producer that restarts gets a higher epoch, and the broker
  rejects its zombie predecessor. Fencing at the client level.

**Decision.** Three nodes in **combined mode** (each is both broker and controller). That is the
smallest quorum that tolerates a failure, and it fits the RAM budget. The exercises:

- kill the active controller and watch a new one get elected;
- kill two of three controllers and watch metadata changes stop while existing partitions keep
  serving.

That second case is the difference between *the quorum* and *the data path*.

**Cost.** Combined mode is not recommended for large production clusters, because controller
load competes with broker load. At our scale it is the standard dev and test setup.

**Status.** Planned, step 3.

---

## 12. Transactions: `reschedule_delivery` becomes a real write

*Ch.7 isolation levels, lost updates, write skew, atomic writes, explicit locking.*

**The idea.** Concurrent transactions that read, then decide, then write can corrupt data even
when each one is correct alone:

- **Lost update:** two read-modify-write cycles, and one overwrites the other.
- **Write skew:** two transactions each read a condition, both decide it holds, and both write.
  Together they make the condition false.

Postgres's default **READ COMMITTED** prevents neither. The fixes are, in increasing order of
generality:

- an **atomic conditional write** (`UPDATE … WHERE condition`);
- **explicit locking** (`SELECT … FOR UPDATE`);
- **SERIALIZABLE** isolation, which Postgres implements as Serializable Snapshot Isolation and
  which aborts a transaction that would break serial order, so the caller must retry.

**Why it applies here.** `reschedule_delivery`
(`services/mcp-servers/acme_delivery.py:89`) is currently read-only on purpose: it checks the
fixture and returns `ok` without writing. Once it writes to Postgres, its logic becomes
*check-then-act*:

```
read shipment            -> status = in_transit, reschedulable
decide: allowed
write new delivery date
```

Meanwhile the depot's dispatch system (simulated) marks the parcel `out_for_delivery`. If dispatch
commits between our read and our write, the parcel leaves on the van with a date the driver doesn't
know about, and the agent has told the caller "done, it's Friday". Neither transaction did
anything wrong on its own.

**Decision.** The write is a **single atomic conditional statement**:

```sql
UPDATE shipments SET estimated_delivery = $2
 WHERE shipment_id = $1
   AND status NOT IN ('delivered', 'out_for_delivery')
RETURNING ...;
```

Zero rows updated means `too_late`, and that decision is made by the database, under its row lock,
with no window between check and act. The exercise reproduces the race first, with READ COMMITTED
and a separate SELECT then UPDATE, with the simulator firing concurrent reschedules and dispatches.
Then it compares the three fixes.

**Cost.** Conditional writes push business rules into SQL. SERIALIZABLE would keep them in Python,
at the cost of retry logic and aborts under contention.

**Status.** Planned, step 6.

---

## 13. Derived data, dual writes, and the outbox

*Ch.11 change data capture, keeping systems in sync. Ch.12 derived data, unbundling databases.*

**The idea.** DDIA separates **systems of record** (the authoritative copy) from **derived data**
(caches, indexes, aggregates, views), which can always be rebuilt from the source. The central
warning concerns **dual writes**: application code that writes the same change to two systems
(say, Postgres and Kafka). If the process crashes between the two writes, they disagree forever.
If two processes race, the two systems can apply the changes in *different orders*, and also
disagree forever. Neither error is ever reported.

The fix is to write to **one** place and *derive* the rest from its log:

- **Change data capture (CDC)**, which reads the database's own replication log;
- the **transactional outbox**: write the event row into an `outbox` table *in the same
  transaction* as the business change. CDC (Debezium) then relays the outbox to Kafka.

DDIA's larger view (ch.12, "unbundling the database") treats the whole data system as a log
plus derived views, where every index, cache and table is a materialized view you can drop and
rebuild.

**Why it applies here.** Once `reschedule_delivery` writes (§12), we also want a
`DeliveryRescheduled` event, for analytics and to notify the depot. The obvious code is:

```python
db.execute(UPDATE ...)        # 1
kafka.produce(Rescheduled)    # 2   <- crash here: DB changed, nobody notified
```

Swapping the order doesn't help either: the event is announced, then the DB write fails.

**Decisions.**

- **Call events (§1–§9): direct produce, loss tolerated.** They are *observations* of a process
  whose state is in memory. There is no database to be consistent with, and the §1 trade-off
  applies.
- **Business changes (step 8): outbox + Debezium.** The reschedule and its event commit
  atomically, or not at all. Debezium tails the Postgres WAL and publishes. Delivery is
  at-least-once, so consumers dedupe (§7).
- **Every derived view must be rebuildable** from Kafka plus the lake: Postgres analytics tables,
  live metrics, the compacted summary topic. The exercise in step 10 is to *drop* a derived table,
  replay from offset 0 (or from the lake), and check that the rebuild matches. A view that can't be
  rebuilt is a second system of record that nobody chose.

**Cost.** The outbox adds a table, a CDC connector and a relay hop (hundreds of ms of latency
before the event appears). Rebuildability rules out shortcuts like hand-editing a derived table to
"fix" a number.

**Status.** Planned: steps 8 and 10.

---

## 14. Storage engines: OLTP vs OLAP

*Ch.3 B-trees vs LSM-trees, write amplification, column-oriented storage.*

**The idea.** There are two families of storage engine:

- **B-trees** (Postgres): update pages in place, keep a write-ahead log, and give fast point reads.
- **Log-structured / LSM** (RocksDB, Cassandra; Kafka's segments are the simplest form): append
  only, merge in the background, and give fast writes.

Separately, **OLTP** workloads (many small reads and writes by key) and **OLAP** workloads (scans
of many rows, few columns, aggregated) want different layouts. **Column stores** (Parquet, Delta)
read only the columns a query needs, and compress very well because a column holds similar
values.

**Why it applies here.** We will have all of these at once, each doing the job it is suited to:

| Store | Engine | Workload here |
| --- | --- | --- |
| Postgres | B-tree + WAL | tool lookups by `shipment_id`: point reads on the call path |
| Kafka | append-only segment log + sparse offset index | event ingestion: sequential writes, sequential reads |
| Stream job state | RocksDB (LSM) | per-key window state: write-heavy, local |
| Lake | Parquet / Delta (columnar) | "escalation rate by tenant by week": scans |

The tempting shortcut is to run analytics on the operational Postgres. A month-long scan of
`turns` competes for I/O and buffer cache with the point reads that `lookup_shipment` needs within
its 3 s timeout.

**Decision.** OLTP stays in Postgres. Analytics reads from the lake. The exercise runs the same
aggregate on Postgres rows and on Parquet with DuckDB (an in-process columnar engine that fits a
laptop), and measures bytes read and time. ULID keys (§7) keep B-tree inserts at the right-hand
edge of the index.

**Cost.** Two copies of the data, one of them derived (§13) and not always perfectly fresh.

**Status.** Planned, step 10.

---

## 15. Batch processing: immutable inputs, re-runnable outputs

*Ch.10 the Unix philosophy, MapReduce and dataflow engines, joins, the output of batch workflows,
human fault tolerance.*

**The idea.** Batch jobs read **immutable** input and produce new output, leaving the input
untouched. That gives *human fault tolerance*: when you ship a bug, you fix the code and re-run,
and the bad output is replaced. DDIA covers join strategies:

- **sort-merge** joins: both sides are large;
- **broadcast hash** joins: one side is small enough to send to every worker;
- **partitioned hash** joins: both sides are partitioned the same way.

**Why it applies here.** Our gold metrics will have bugs. The first version of "escalation rate"
will count something wrong, and we will find out a month later. If daily jobs *append*, a re-run
duplicates rows, and fixing history means hand-written deletes. If daily jobs *overwrite their
date partition*, a re-run is the fix.

The join shapes also come up directly. Events × `dim_tenant` (3 rows) is a **broadcast** join.
Events × last month's events (sessionising late data) is a **sort-merge** join.

**Decisions.**

- The lake is partitioned by `date` and `tenant_id`.
- Every Airflow task writes **exactly one partition, by overwrite**, and is idempotent. Running a
  task twice equals running it once. Backfill is a supported operation, not an incident.
- Bronze is raw and append-only (after PII redaction, §17). Silver is deduplicated (§7). Gold is
  aggregated. Any layer can be rebuilt from the layer below.

**Cost.** Overwrite-by-partition requires the job to recompute the *whole* partition, not just
the new rows. Delta's `MERGE` gives incremental upserts, but makes jobs harder to reason about.
We start with overwrite.

**Status.** Planned, steps 10–11.

---

## 16. Stream processing: windows, joins, late data

*Ch.11 event time vs processing time, windows, stream joins, fault tolerance.*

**The idea.**

- **Window by event time, not processing time.** A consumer that was down for an hour and
  catches up would otherwise put an hour of events into one minute's window.
- **Event time brings late events.** A **watermark** declares "no events older than T are
  expected", and policy decides what to do with the stragglers.
- **Window types:** tumbling, hopping, sliding, and **session** windows, which have no fixed
  length and close after a gap of inactivity.
- **Three joins:**
  - **stream-stream**: match two event types within a time bound;
  - **stream-table**: enrich events with a slowly changing table, kept up to date through CDC or
    a compacted topic;
  - **table-table**: maintain a materialized join.
- **Fault tolerance** uses checkpoints plus idempotent sinks.

**Why it applies here.**

- **A call is a session window** keyed by `call_id`. We have explicit `CallStarted` / `CallEnded`
  events, but a crashed edge never sends `CallEnded`. The session gap (say 2 minutes of silence on
  the key) closes those orphaned calls, and the gap-closed count is a metric of how often we crash
  mid-call.
- **Late events are certain**, because §1's producers buffer during Kafka hiccups. The watermark
  must allow for the buffer's drain time.
- **Stream-stream join:** `SpeechEnded` ↔ the next `FirstAudioPlayed` for the same call gives
  "caller fell silent → heard a reply", which is the latency that matters to the caller.
- **Stream-table join:** events × tenant config (flows, slot limits). Tenants change by editing
  YAML. When that config moves to Postgres, CDC keeps the stream's copy current, and late events
  are judged against the config *as it was*, not as it is now.
- **Live metrics:** tumbling 1-minute windows per tenant for escalation rate and tool error rate,
  plus histograms (§2) for latency.

**Decision.** Spark Structured Streaming first, because its API carries over to Databricks. Flink
is optional later. Event-time windows with watermarks, checkpointing to local disk, and sinks that
upsert on `event_id` or on the window key.

**Cost.** Watermarks trade completeness for latency: a longer wait means more correct numbers,
published later. Session windows hold state for every open call.

**Status.** Planned, step 7.

---

## 17. Personal data in an immutable log

*Ch.12 doing the right thing: privacy, data minimisation, immutability vs deletion.*

**The idea.** DDIA ends on ethics. Data about people is a liability as well as an asset, and the
techniques in the rest of the book (immutable logs, replicated copies, derived data everywhere)
make it **harder to delete**. An immutable log conflicts directly with a legal right to erasure.
DDIA mentions the options:

- don't keep the data in the first place (data minimisation);
- separate identifying data so it can be deleted on its own;
- **crypto-shredding**: encrypt per subject and delete the key.

**Why it applies here.** Callers say their phone numbers and shipment IDs out loud, which makes
them part of transcripts. Tool results contain names (`signed_by: "R. Dupont"`). One tenant is
French, and the fixtures reference Lyon and Marseille: GDPR territory. With §13's "every view is
rebuildable from the log", a phone number that reaches bronze is copied into silver, gold, every
replica, and every rebuild, indefinitely.

**Decisions.**

- **PII class is declared per field in the schema** with a custom option, `[(pii) = ...]`:
  - `NONE`: keep;
  - `IDENTIFIER` (shipment ID, phone): hash before bronze, which still allows joins;
  - `FREE_TEXT` (transcripts, spoken replies, error details): restricted, with retention limits;
  - `PERSONAL` (names, raw tool payloads): drop before bronze.
- **Redaction is driven by the descriptor**, not by a hand-maintained list. The sink reads the
  option on each field. A new field without a classification fails the test
  (`data/tests/test_events.py`), so a new field can't leak unnoticed.
- **Kafka is the only place raw PII lives, with bounded retention (7 days).** The lake receives
  redacted data. Immutability is then safe *downstream*, because there is nothing to erase there.
- Crypto-shredding of free text is a possible later exercise, if transcripts must be kept long
  term.

**Cost.** Hashed identifiers can't be read back. "Show me all calls about parcel AC4471X209"
needs the hash of the ID, not the ID. Dropping `result_json` loses detail that might later look
valuable. That is data minimisation working as intended.

**Status.** Classification done (step 2). Redaction in the bronze sink, step 8–10.

---

## Summary

| # | Decision | DDIA | Protects against | Status |
| --- | --- | --- | --- | --- |
| 1 | Async bounded producers, drop and count | 1, 11 | an analytics outage becoming a call outage | step 4 |
| 2 | Per-stage durations, merged histograms | 1 | averaged percentiles, invisible tails | fields done |
| 3 | Protobuf, frozen tags, `FULL_TRANSITIVE` registry | 4 | mixed versions during rollouts and replays | done / step 3 |
| 4 | Past-tense facts, one envelope, one topic per stream | 11, 12 | a call's history scattered across logs | done |
| 5 | Wall / monotonic / sample clocks, each for one job | 8 | clock skew corrupting latency and order | done |
| 6 | `seq` per producer, key by call, order by sample | 9 | a sequencer on the call path | done |
| 7 | `event_id` + idempotent sinks | 11, 12 | duplicates from restarts and replays | done / steps 4, 7 |
| 8 | Key by `call_id`, 6 partitions fixed up front | 6 | tenant hot spots, reordering on repartition | step 3 |
| 9 | Separate partials topic, audio by reference, compacted summary | 3, 11 | the noisiest stream dictating retention | step 3 |
| 10 | RF=3, `acks=all`, `min.insync=2`; read primary after write | 5 | lost acked events; stale reads after reschedule | steps 3, 9 |
| 11 | 3-node KRaft quorum | 9 | split brain, zombie leaders | step 3 |
| 12 | Atomic conditional `UPDATE` | 7 | reschedule racing dispatch | step 6 |
| 13 | Outbox + CDC for business changes; views rebuildable | 11, 12 | dual-write divergence | steps 8, 10 |
| 14 | OLTP in Postgres, OLAP in the lake | 3 | analytics scans starving tool lookups | step 10 |
| 15 | Overwrite-by-partition, idempotent batch | 10 | re-runs duplicating history | steps 10–11 |
| 16 | Event-time windows, session window per call | 11 | catch-up skewing live metrics, orphaned calls | step 7 |
| 17 | PII declared in schema, redacted before bronze | 12 | personal data copied into every derived view | done / steps 8–10 |
