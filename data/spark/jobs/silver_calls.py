"""Silver: bronze call events -> clean, deduplicated, masked tables on the lake.

    SPARK_REMOTE=sc://localhost:15002 .venv/bin/python data/spark/jobs/silver_calls.py
    ... silver_calls.py --since 2026-11-23      # only bronze ingested since that day

Three Delta tables, all written with MERGE, so a run can be repeated (or two
runs can overlap the same bronze rows) without ever adding a row twice:

  silver/call_events   one row per event_id. Bronze keeps every Kafka record,
                       duplicates included (a producer retry is a second
                       record); here each event exists exactly once (DDIA.md:
                       effectively-once = at-least-once + idempotent sink).
                       Free-text fields have identifiers masked (see below).
                       Partitioned by event date, i.e. when it HAPPENED.
  silver/calls         one row per call: who, when, how it ended, and the
                       facts the quality checks need (seq gaps, start/end).
                       Recomputed from all of a call's events whenever any of
                       them arrives, then upserted: a late CallEnded updates
                       the row instead of creating a second one.
  silver/turns         one row per (call, turn): routing, LLM timing and
                       tokens, tool outcome and latency, barge-in.

Three more, for the tenant Insights page. Each row carries tenant_id,
call_date and synthetic copied from its CALL (silver/calls), never from the
event's own envelope or clock: a sentiment summary emitted an hour after the
call (the sentiment service's idle timeout) still counts on the call's day.

  silver/utterance_emotions  one row per EmotionClassified (calls.sentiment.v1,
                             same bronze table): the caller's emotion on one
                             utterance.
  silver/call_sentiment      one row per call with a CallEmotionSummarized:
                             dominant, first and last emotion, trend, risk.
  silver/knowledge_searches  one row per KnowledgeSearched: a platform client's
                             documents searched for what the caller asked.
                             The query is free text, masked like the rest.

All three are recomputed per touched call, like calls and turns. A table that
does not exist yet is built once from ALL of silver/call_events, so adding it
to a running lake backfills its history instead of starting today.

Masking. Bronze hashes PII_IDENTIFIER fields and drops PII_PERSONAL ones, but
keeps PII_FREE_TEXT (transcripts, the agent's spoken replies) as written,
because what a caller says can be anything. Callers read phone numbers out, and
the agent echoes shipment IDs back. Silver masks those patterns in every
PII_FREE_TEXT field, found from the (pii) options in events.proto like bronze
does. The silver quality checks then verify nothing slipped through.
Known limit: a code spelled out in words ("four four seven one") is not caught.
"""

from __future__ import annotations

import argparse
import sys

from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F

try:                    # run as a script: data/spark/jobs is on sys.path
    from bronze_call_events import BRONZE, EVENT, LAKE, PII, connect, pii_of
except ImportError:     # imported by Airflow as jobs.silver_calls
    from jobs.bronze_call_events import BRONZE, EVENT, LAKE, PII, connect, pii_of

SILVER = f"{LAKE}/silver"
CALL_EVENTS = f"{SILVER}/call_events"
CALLS = f"{SILVER}/calls"
TURNS = f"{SILVER}/turns"
UTTERANCE_EMOTIONS = f"{SILVER}/utterance_emotions"
CALL_SENTIMENT = f"{SILVER}/call_sentiment"
KNOWLEDGE_SEARCHES = f"{SILVER}/knowledge_searches"

# Java regexes (Spark). Order matters: emails before phone numbers.
MASKS = [
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email]"),
    # 9+ digits with optional separators: phone and account numbers. Not 8, so
    # a date like 2026-10-03 survives.
    (r"(?<![A-Za-z0-9])(?:\d[ .\-]?){8,}\d(?![A-Za-z0-9])", "[number]"),
    # 8-12 letters and digits, at least one of each: shipment IDs and the like.
    (r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{8,12}\b", "[code]"),
]

def enum_name(col: str, prefix: str) -> Column:
    """from_protobuf decodes enums as their names: END_REASON_ESCALATED -> 'escalated'."""
    return F.lower(F.regexp_replace(F.col(col), f"^{prefix}", ""))


def free_text_paths() -> list[str]:
    """payload-relative paths of every PII_FREE_TEXT field, e.g. turn_completed.spoken_text."""
    paths = []
    for oneof_field in EVENT.oneofs_by_name["payload"].fields:
        for f in oneof_field.message_type.fields:
            if pii_of(f) == PII["PII_FREE_TEXT"]:
                paths.append(f"{oneof_field.name}.{f.name}")
    return paths


def mask(text: Column) -> Column:
    for pattern, token in MASKS:
        text = F.regexp_replace(text, pattern, token)
    return text


def masked_payload() -> Column:
    """payload with every free-text field masked.

    Each event type's struct is replaced in place, once. Wrapping the whole
    payload in when(...).otherwise(...) per field instead doubles the expression
    each time: five fields made 32 copies of a ~19-message struct, and the
    Spark driver ran out of heap planning it.
    """
    by_parent: dict[str, list[str]] = {}
    for path in free_text_paths():
        parent, field = path.split(".")
        by_parent.setdefault(parent, []).append(field)
    p = F.col("payload")
    for parent, fields in by_parent.items():
        struct = F.col(f"payload.{parent}")
        for field in fields:
            struct = struct.withField(field, mask(F.col(f"payload.{parent}.{field}")))
        # Events of other types have this struct null; keep it null.
        p = p.withField(parent, F.when(F.col(f"payload.{parent}").isNotNull(), struct))
    return p


def _exists(spark: SparkSession, path: str) -> bool:
    try:
        spark.read.format("delta").load(path).limit(0).collect()
        return True
    except Exception:       # noqa: BLE001 — not a Delta table yet
        return False


def upsert(spark: SparkSession, df: DataFrame, path: str, keys: list[str],
           partition: str, update: bool) -> None:
    """MERGE df into the Delta table at path, creating it on the first run.

    update=False: insert-only, for immutable rows (events). Their partition
                  (event date) can never change, so it joins too and Delta
                  skips files that cannot match.
    update=True:  upsert, for rows recomputed as more events arrive (calls,
                  turns). Their partition CAN change (a late CallStarted moves
                  call_date), so it must not be in the join: a row whose date
                  moved would no longer match, and be inserted a second time.
    """
    if not _exists(spark, path):
        df.write.format("delta").partitionBy(partition).save(path)
        return
    # A new payload type in events.proto adds a field to the `payload` struct
    # (bronze merges it in). Without schema evolution, the MERGE into a table
    # written before the change fails on the struct mismatch.
    spark.conf.set("spark.databricks.delta.schema.autoMerge.enabled", "true")
    view = f"src_{path.rsplit('/', 1)[-1]}"
    df.createOrReplaceTempView(view)
    on = " AND ".join(f"t.{k} = s.{k}" for k in (keys if update else [partition, *keys]))
    matched = "WHEN MATCHED THEN UPDATE SET *" if update else ""
    spark.sql(f"MERGE INTO delta.`{path}` t USING {view} s ON {on} "
              f"{matched} WHEN NOT MATCHED THEN INSERT *")


# -- silver/call_events -----------------------------------------------------------

def call_events(bronze: DataFrame) -> DataFrame:
    """One row per event_id: the first copy Kafka received wins."""
    first = Window.partitionBy("event_id").orderBy("kafka_ts", "partition", "offset")
    return (bronze
            .withColumn("_n", F.row_number().over(first))
            .where("_n = 1")
            .select("event_id", "call_id", "tenant_id", "seq", "occurred_at", "event_type",
                    "source", masked_payload().alias("payload"),
                    "kafka_ts", "ingest_lag_ms",
                    F.to_date("occurred_at").alias("event_date")))


# -- silver/calls -------------------------------------------------------------------

def _first(event_type: str, value: str | Column) -> Column:
    col = F.col(value) if isinstance(value, str) else value
    return F.max(F.when(F.col("event_type") == event_type, col))


def _count(event_type: str) -> Column:
    return F.sum(F.when(F.col("event_type") == event_type, 1).otherwise(0)).cast("int")


def calls(events: DataFrame) -> DataFrame:
    """One row per call, from ALL of its events (not just this run's)."""
    # seq is per producer: each service numbers its own events of a call.
    per_producer = (events.groupBy("call_id", F.col("source.service").alias("service"))
                    .agg(F.min("seq").alias("min_seq"),
                         (F.max("seq") - F.min("seq") + 1 - F.countDistinct("seq"))
                         .alias("gaps")))
    seq = per_producer.groupBy("call_id").agg(
        F.min("min_seq").alias("min_seq"), F.sum("gaps").cast("int").alias("seq_gaps"))

    c = (events.groupBy("call_id").agg(
        F.max("tenant_id").alias("tenant_id"),
        _first("call_started", "payload.call_started.tenant_slug").alias("tenant_slug"),
        _first("call_started", "payload.call_started.locale").alias("locale"),
        _first("call_started", "payload.call_started.channel").alias("channel"),
        _first("call_started", "payload.call_started.synthetic").alias("synthetic"),
        _first("call_started", "occurred_at").alias("started_at"),
        _first("call_ended", "occurred_at").alias("ended_at"),
        _first("call_ended", enum_name("payload.call_ended.reason", "END_REASON_")).alias("end_reason"),
        _first("call_ended", "payload.call_ended.duration_ms").alias("duration_ms"),
        _first("call_ended", "payload.call_ended.turns").alias("turns"),
        F.min("occurred_at").alias("first_event_at"),
        F.max("occurred_at").alias("last_event_at"),
        F.count("*").cast("int").alias("events"),
        _count("call_started").alias("starts"),
        _count("call_ended").alias("ends"),
        (_count("escalated") > 0).alias("escalated"),
        _count("barged_in").alias("barge_ins"),
        _count("tool_invoked").alias("tool_calls"),
        _count("slot_rejected").alias("slot_rejections"),
        _count("error_raised").alias("errors"),
        F.max(F.col("ingest_lag_ms")).alias("max_ingest_lag_ms"),
        F.min(F.col("ingest_lag_ms")).alias("min_ingest_lag_ms"),
    ))
    return (c.join(seq, "call_id")
            .withColumn("call_date", F.to_date(F.coalesce("started_at", "first_event_at"))))


# -- silver/turns -------------------------------------------------------------------

TURN_PAYLOADS = ["turn_decided", "slot_filled", "slot_rejected", "endpointing_changed",
                 "tool_invoked", "llm_completed", "turn_completed", "barged_in", "escalated"]


def turns(events: DataFrame) -> DataFrame:
    turn_index = F.coalesce(*[F.col(f"payload.{p}.turn_index") for p in TURN_PAYLOADS])
    t = events.where(F.col("event_type").isin(TURN_PAYLOADS)).withColumn("turn_index", turn_index)
    return (t.groupBy("call_id", "turn_index").agg(
        F.max("tenant_id").alias("tenant_id"),
        _first("turn_decided", enum_name("payload.turn_decided.kind", "TURN_KIND_")).alias("kind"),
        _first("turn_decided", "payload.turn_decided.intent").alias("intent"),
        _first("turn_decided", "payload.turn_decided.route_score").alias("route_score"),
        _first("llm_completed", "payload.llm_completed.purpose").alias("llm_purpose"),
        _first("llm_completed", "payload.llm_completed.ttft_ms").alias("llm_ttft_ms"),
        _first("llm_completed", "payload.llm_completed.total_ms").alias("llm_total_ms"),
        _first("llm_completed", "payload.llm_completed.prompt_tokens").alias("prompt_tokens"),
        _first("llm_completed", "payload.llm_completed.completion_tokens").alias("completion_tokens"),
        _first("llm_completed", "payload.llm_completed.cancelled").alias("llm_cancelled"),
        _first("tool_invoked", "payload.tool_invoked.tool").alias("tool"),
        _first("tool_invoked", "payload.tool_invoked.outcome").alias("tool_outcome"),
        _first("tool_invoked", "payload.tool_invoked.latency_ms").alias("tool_latency_ms"),
        _first("tool_invoked", "payload.tool_invoked.timed_out").alias("tool_timed_out"),
        _first("turn_completed", "payload.turn_completed.total_ms").alias("turn_total_ms"),
        _first("turn_completed", "payload.turn_completed.clauses").alias("clauses"),
        _first("turn_completed", "payload.turn_completed.spoken_text").alias("spoken_text"),
        _count("slot_filled").alias("slots_filled"),
        _count("slot_rejected").alias("slots_rejected"),
        (_count("barged_in") > 0).alias("barged_in"),
        (_count("escalated") > 0).alias("escalated"),
        F.min("occurred_at").alias("started_at"),
    ).withColumn("turn_date", F.to_date("started_at")))


# -- insights: sentiment and knowledge, dated by their call ----------------------------

def _call_context(c: DataFrame) -> DataFrame:
    """What a derived row takes from its call: who, which day, which traffic."""
    return c.select("call_id", "tenant_id", "call_date", "synthetic")


def utterance_emotions(events: DataFrame, c: DataFrame) -> DataFrame:
    """One row per EmotionClassified, keyed by its event_id.

    utterance_index is the sentiment service's own count, which restarts if
    that service restarts mid-call: unique per call in practice, not by contract.
    """
    p = "payload.emotion_classified"
    return (events.where(F.col("event_type") == "emotion_classified")
            .select("call_id", "event_id",
                    F.col(f"{p}.utterance_index").alias("utterance_index"),
                    F.col(f"{p}.utterance_event_id").alias("utterance_event_id"),
                    F.col(f"{p}.emotion").alias("emotion"),
                    F.col(f"{p}.score").alias("score"),
                    F.col(f"{p}.model").alias("model"),
                    F.col("occurred_at").alias("classified_at"))
            .join(_call_context(c), "call_id"))


def call_sentiment(events: DataFrame, c: DataFrame) -> DataFrame:
    """One row per call: its CallEmotionSummarized (the latest, should there be two)."""
    p = "payload.call_emotion_summarized"
    summary = F.struct(
        F.col(f"{p}.utterances").alias("utterances"),
        F.col(f"{p}.dominant").alias("dominant"),
        F.col(f"{p}.first").alias("first_emotion"),
        F.col(f"{p}.last").alias("last_emotion"),
        F.col(f"{p}.trend").alias("trend"),
        F.col(f"{p}.escalation_risk").alias("escalation_risk"),
        F.col(f"{p}.model").alias("model"),
        F.col("occurred_at").alias("summarized_at"))
    s = (events.where(F.col("event_type") == "call_emotion_summarized")
         .groupBy("call_id")
         .agg(F.max_by(summary, F.col("occurred_at")).alias("s"))
         .select("call_id", "s.*"))
    return s.join(_call_context(c), "call_id")


def knowledge_searches(events: DataFrame, c: DataFrame) -> DataFrame:
    """One row per KnowledgeSearched. `query` is already masked (masked_payload)."""
    p = "payload.knowledge_searched"
    return (events.where(F.col("event_type") == "knowledge_searched")
            .select("call_id", "event_id",
                    F.col(f"{p}.turn_index").alias("turn_index"),
                    F.col(f"{p}.query").alias("query"),
                    F.col(f"{p}.hits").alias("hits"),
                    F.col(f"{p}.best_similarity").alias("best_similarity"),
                    F.col(f"{p}.covered").alias("covered"),
                    F.col(f"{p}.failed").alias("failed"),
                    F.coalesce(F.col(f"{p}.document_titles"),
                               F.array().cast("array<string>")).alias("document_titles"),
                    F.col(f"{p}.latency_ms").alias("latency_ms"),
                    F.col("occurred_at").alias("searched_at"))
            .join(_call_context(c), "call_id"))


# (path, builder, MERGE keys, the payload member it reads). Partitioned by
# call_date and upserted (update=True): a call's date can still move.
INSIGHTS = [
    (UTTERANCE_EMOTIONS, utterance_emotions, ["event_id"], "emotion_classified"),
    (CALL_SENTIMENT, call_sentiment, ["call_id"], "call_emotion_summarized"),
    (KNOWLEDGE_SEARCHES, knowledge_searches, ["event_id"], "knowledge_searched"),
]


def _payload_has(events: DataFrame, member: str) -> bool:
    """False on a silver/call_events written before `member` joined events.proto."""
    return member in events.schema["payload"].dataType.fieldNames()


def build_insights(spark: SparkSession, events: DataFrame, call_rows: DataFrame) -> None:
    """Upsert the insight tables for the calls in `events` (all of their events)."""
    for path, build, keys, member in INSIGHTS:
        src_events, src_calls = events, call_rows
        if not _exists(spark, path):
            # First run with this table: backfill from every call, not just this batch.
            src_events = spark.read.format("delta").load(CALL_EVENTS)
            src_calls = spark.read.format("delta").load(CALLS)
        if not _payload_has(src_events, member):
            continue        # that event type never reached silver: no columns to read yet
        upsert(spark, build(src_events, src_calls), path, keys, "call_date", update=True)


# -- the run ------------------------------------------------------------------------

def run(spark: SparkSession, since: str | None) -> dict:
    bronze = spark.read.format("delta").load(BRONZE)
    if since:
        bronze = bronze.where(F.col("ingest_date") >= F.lit(since).cast("date"))
    batch = call_events(bronze)

    upsert(spark, batch, CALL_EVENTS, ["event_id"], "event_date", update=False)

    # Every call touched by this batch, recomputed from all of its events.
    touched = batch.select("call_id").distinct()
    events = spark.read.format("delta").load(CALL_EVENTS).join(touched, "call_id", "left_semi")
    call_rows = calls(events)
    upsert(spark, call_rows, CALLS, ["call_id"], "call_date", update=True)
    upsert(spark, turns(events), TURNS, ["call_id", "turn_index"], "turn_date", update=True)
    build_insights(spark, events, call_rows)

    tables = (("call_events", CALL_EVENTS), ("calls", CALLS), ("turns", TURNS),
              ("utterance_emotions", UTTERANCE_EMOTIONS), ("call_sentiment", CALL_SENTIMENT),
              ("knowledge_searches", KNOWLEDGE_SEARCHES))
    return {name: spark.read.format("delta").load(path).count()
            for name, path in tables if _exists(spark, path)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="only bronze rows ingested on or after this date "
                                    "(YYYY-MM-DD); default: all of bronze")
    args = ap.parse_args()
    stats = run(connect(), args.since)
    print("silver rows:", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
