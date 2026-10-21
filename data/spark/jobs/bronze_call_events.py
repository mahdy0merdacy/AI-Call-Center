"""Bronze: calls.events.v1 -> Delta on the lake, decoded and redacted.

    SPARK_REMOTE=sc://localhost:15002 .venv/bin/python data/spark/jobs/bronze_call_events.py

Runs from anywhere that can reach the Spark Connect server: this venv, the
Airflow container, later a pod. Only the PLAN travels over gRPC; Spark itself
reads Kafka and writes the lake, so addresses below (kafka-1:19092,
s3a://lake) are as seen from the Spark server, not from this client.

What a run does (DDIA.md §7, §15, §17):
  - Streaming read with trigger=availableNow: process everything new since the
    last run, then stop. The checkpoint holds the Kafka offsets, so re-running
    is safe and a crash mid-run resumes, never re-appends: the Delta sink
    commits each micro-batch with its batch id and skips one it already wrote.
  - Strips the 6-byte Confluent header (magic, schema id, message index) and
    decodes with the descriptor from data/gen/events.desc.
  - Redacts BEFORE anything is written: PII_PERSONAL fields are dropped,
    PII_IDENTIFIER fields are replaced by a peppered SHA-256. Driven by the
    (pii) options in events.proto, not by a list here.
  - Records that are not our wire format, or do not decode, go to a quarantine
    table instead of failing the run or vanishing.

Bronze is partitioned by INGEST date, not event date. A late event lands in
today's partition, so a past partition is never rewritten. Silver re-sorts by
event time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pyspark.sql import Column, DataFrame, SparkSession, functions as F
from pyspark.sql.protobuf.functions import from_protobuf

HERE = Path(__file__).resolve().parent
# data/gen when run from the repo; /opt/aicc/gen inside the Airflow container.
GEN = next(p for p in (Path(os.environ.get("AICC_GEN", "/nonexistent")),
                       HERE.parents[1] / "gen",    # repo: data/spark/jobs -> data/gen
                       HERE.parents[0] / "gen")    # container: /opt/aicc/jobs -> /opt/aicc/gen
           if (p / "events.desc").exists())


def _load_schema():
    """The Event descriptor and the (pii) extension, from events.desc alone.

    Built at runtime from the descriptor set instead of importing the
    generated events_pb2: generated code pins a minimum protobuf runtime
    (7.35 here), and Airflow ships its own. The descriptor set works with any
    runtime — the same file Spark's from_protobuf reads.
    """
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    fds = descriptor_pb2.FileDescriptorSet.FromString((GEN / "events.desc").read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for fd in fds.file:
        pool.Add(fd)
    pkg = "aicallcenter.events.v1"
    ext = pool.FindExtensionByName(f"{pkg}.pii")
    # A field's options arrive as the default pool's FieldOptions, which does
    # not know our extension. Re-parse the bytes as THIS pool's FieldOptions.
    options_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName("google.protobuf.FieldOptions"))

    def pii_of(field) -> int | None:
        opts = options_cls.FromString(field.GetOptions().SerializeToString())
        return opts.Extensions[ext] if opts.HasExtension(ext) else None

    return (pool.FindMessageTypeByName(f"{pkg}.Event"), pii_of,
            {v.name: v.number for v in pool.FindEnumTypeByName(f"{pkg}.Pii").values})


EVENT, pii_of, PII = _load_schema()

KAFKA = os.environ.get("KAFKA_BOOTSTRAP", "kafka-1:19092")
TOPIC = "calls.events.v1"
LAKE = os.environ.get("LAKE", "s3a://lake")
BRONZE = f"{LAKE}/bronze/call_events"
QUARANTINE = f"{LAKE}/bronze/call_events_quarantine"
# Checkpoints live on the Spark server's persistent volume, not the lake:
# Spark's checkpoint protocol expects real directories and atomic rename, and
# SeaweedFS's S3 listing makes an empty directory look non-empty. Small,
# single-writer driver state — on k3s this is the Spark pod's PVC.
CHECKPOINTS = os.environ.get("CHECKPOINT_ROOT",
                             "file:///opt/spark/work-dir/checkpoints") + "/bronze_call_events"
MESSAGE = "aicallcenter.events.v1.Event"
ENVELOPE = ["event_id", "call_id", "tenant_id", "seq", "occurred_at_ms", "source"]
PAYLOADS = [f.name for f in EVENT.oneofs_by_name["payload"].fields]


def connect() -> SparkSession:
    return SparkSession.builder.remote(
        os.environ.get("SPARK_REMOTE", "sc://localhost:15002")).getOrCreate()


def pii_plan() -> tuple[list[str], list[str]]:
    """(paths to drop, paths to hash), read from the (pii) field options."""
    drop, hashed = [], []

    def walk(desc, prefix: str):
        for f in desc.fields:
            path = f"{prefix}{f.name}"
            if f.message_type is not None:
                walk(f.message_type, path + ".")
                continue
            cls = pii_of(f)
            if cls == PII["PII_PERSONAL"]:
                drop.append(path)
            elif cls == PII["PII_IDENTIFIER"]:
                hashed.append(path)

    walk(EVENT, "")
    return drop, hashed


def redact(e: Column, src: str) -> Column:
    """Apply pii_plan() to the decoded struct column `e` (named `src`)."""
    drop, hashed = pii_plan()
    # A bare SHA-256 of a 10-digit phone number is reversible by brute force in
    # seconds. The secret pepper is what makes the hash a pseudonym.
    pepper = F.lit(os.environ.get("PII_PEPPER", "dev-pepper-change-me"))
    for path in hashed:
        value = F.col(f"{src}.{path}")
        e = e.withField(path, F.when(value.isNotNull(),
                                     F.sha2(F.concat(pepper, value), 256)))
    return e.dropFields(*drop) if drop else e


def decoded(spark: SparkSession) -> DataFrame:
    raw = (spark.readStream.format("kafka")
           .option("kafka.bootstrap.servers", KAFKA)
           .option("subscribe", TOPIC)
           .option("startingOffsets", "earliest")
           .option("maxOffsetsPerTrigger", 50_000)
           .load())
    desc = (GEN / "events.desc").read_bytes()
    framed = F.expr("substring(value, 1, 1) = X'00' AND substring(value, 6, 1) = X'00'")
    body = F.expr("substring(value, 7, length(value) - 6)")
    return raw.select(
        "topic", "partition", "offset",
        F.col("timestamp").alias("kafka_ts"),          # LogAppendTime: broker clock
        F.col("value"),
        framed.alias("framed"),
        F.conv(F.hex(F.expr("substring(value, 2, 4)")), 16, 10).cast("int").alias("schema_id"),
        F.when(framed, from_protobuf(
            body, MESSAGE, binaryDescriptorSet=desc,
            options={"mode": "PERMISSIVE", "upcast.unsigned.ints": "true"})).alias("e"),
    )


def bronze_rows(df: DataFrame) -> DataFrame:
    e = redact(F.col("e"), "e")
    event_type = F.coalesce(*[F.when(F.col(f"e.{p}").isNotNull(), F.lit(p))
                              for p in PAYLOADS])
    occurred = F.timestamp_millis(F.col("e.occurred_at_ms"))
    return (df.where(F.col("e").isNotNull())
            .select(
                F.col("e.event_id").alias("event_id"),
                F.col("e.call_id").alias("call_id"),
                F.col("e.tenant_id").alias("tenant_id"),
                F.col("e.seq").alias("seq"),
                occurred.alias("occurred_at"),
                event_type.alias("event_type"),
                e.getField("source").alias("source"),
                e.dropFields(*ENVELOPE).alias("payload"),
                "topic", "partition", "offset", "kafka_ts", "schema_id",
                # Producer clock vs broker clock: lag plus skew (DDIA.md §5).
                (F.unix_millis("kafka_ts") - F.col("e.occurred_at_ms")).alias("ingest_lag_ms"),
                F.to_date("kafka_ts").alias("ingest_date")))


def quarantine_rows(df: DataFrame) -> DataFrame:
    return (df.where(F.col("e").isNull())
            .select("topic", "partition", "offset", "kafka_ts", "schema_id", "value",
                    F.when(~F.col("framed"), F.lit("not_confluent_wire_format"))
                     .otherwise(F.lit("protobuf_decode_failed")).alias("reason"),
                    F.to_date("kafka_ts").alias("ingest_date")))


def ingest(spark: SparkSession) -> dict:
    """One availableNow run of both sinks. Returns rows written per sink."""
    df = decoded(spark)
    queries = {
        name: (rows.writeStream.format("delta")
               .option("checkpointLocation", f"{CHECKPOINTS}/{name}")
               .partitionBy("ingest_date")
               .trigger(availableNow=True)
               .queryName(f"bronze_call_events_{name}")
               .start(path))
        for name, rows, path in (("events", bronze_rows(df), BRONZE),
                                 ("quarantine", quarantine_rows(df), QUARANTINE))
    }
    # Each query reads Kafka on its own (own offsets, own checkpoint), so the
    # count is Kafka records read this run, not rows written.
    stats = {}
    for name, q in queries.items():
        q.awaitTermination()
        stats[f"{name}_kafka_records_read"] = sum(
            p["numInputRows"] or 0 for p in q.recentProgress)
    stats["bronze_total"] = spark.read.format("delta").load(BRONZE).count()
    stats["quarantine_total"] = spark.read.format("delta").load(QUARANTINE).count()
    return stats


if __name__ == "__main__":
    spark = connect()
    print(ingest(spark))
    (spark.read.format("delta").load(BRONZE)
     .groupBy("ingest_date", "event_type").count()
     .orderBy("ingest_date", "event_type").show(truncate=False))
