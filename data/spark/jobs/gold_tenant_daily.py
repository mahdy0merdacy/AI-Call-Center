"""Gold: silver calls and turns -> one row of KPIs per tenant, day and traffic type.

    SPARK_REMOTE=sc://localhost:15002 .venv/bin/python data/spark/jobs/gold_tenant_daily.py
    ... gold_tenant_daily.py --since 2026-11-22     # rebuild those days only

What a tenant (and our dashboards) asks of the system: how many calls, how
many the agent handled without a human, how fast it answered, how often its
tools worked. `synthetic` is part of the key, not a filter applied later:
simulator traffic and real callers are separate rows, so a load test can never
silently become "escalations doubled" (DDIA.md: provenance).

Written as an overwrite of whole days (Delta replaceWhere). Every day in the
window is recomputed from ALL of its silver calls and replaces the old rows in
one atomic commit: a re-run gives the same table, never doubled counts, and a
reader sees the old day or the new one, never half of each (DDIA.md §17: batch
outputs as immutable, replaceable partitions).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

try:                    # run as a script: data/spark/jobs is on sys.path
    from bronze_call_events import LAKE, connect
    from silver_calls import CALLS, TURNS
except ImportError:     # imported by Airflow as jobs.gold_tenant_daily
    from jobs.bronze_call_events import LAKE, connect
    from jobs.silver_calls import CALLS, TURNS

GOLD = f"{LAKE}/gold/tenant_daily"
KEY = ["tenant_id", "call_date", "synthetic"]


def _rate(num, den):
    return F.when(den > 0, num / den)


def tenant_daily(calls: DataFrame, turns: DataFrame) -> DataFrame:
    calls = calls.withColumn("synthetic", F.coalesce("synthetic", F.lit(False)))
    per_call = calls.groupBy(*KEY).agg(
        F.count("*").alias("calls"),
        F.sum(F.when(F.col("ends") > 0, 1).otherwise(0)).alias("ended_calls"),
        F.sum(F.when(F.col("escalated"), 1).otherwise(0)).alias("escalated_calls"),
        F.avg("turns").alias("avg_turns"),
        (F.avg("duration_ms") / 1000).alias("avg_duration_s"),
        F.sum("barge_ins").alias("barge_ins"),
        F.sum("errors").alias("errors"),
    )
    # Turns carry the latency and tool facts; the day comes from their call.
    t = turns.drop("tenant_id").join(calls.select("call_id", *KEY), "call_id")
    per_turn = t.groupBy(*KEY).agg(
        F.count("*").alias("turns"),
        F.percentile_approx("llm_ttft_ms", 0.5).alias("llm_ttft_p50_ms"),
        F.percentile_approx("llm_ttft_ms", 0.95).alias("llm_ttft_p95_ms"),
        F.percentile_approx("turn_total_ms", 0.5).alias("turn_p50_ms"),
        F.percentile_approx("turn_total_ms", 0.95).alias("turn_p95_ms"),
        F.count("tool").alias("tool_calls"),
        F.sum(F.when(F.col("tool").isNotNull() & ~F.coalesce("tool_timed_out", F.lit(False))
                     & (F.col("tool_outcome") != "error"), 1).otherwise(0)).alias("tool_ok"),
        F.sum("slots_filled").alias("slots_filled"),
        F.sum("slots_rejected").alias("slots_rejected"),
    )
    d = per_call.join(per_turn, KEY, "left")
    return d.select(
        *KEY, "calls", "ended_calls", "escalated_calls",
        _rate(F.col("escalated_calls"), F.col("ended_calls")).alias("escalation_rate"),
        # Handled end to end by the agent: ended, and never handed to a human.
        _rate(F.col("ended_calls") - F.col("escalated_calls"), F.col("ended_calls"))
        .alias("self_service_rate"),
        "avg_turns", "avg_duration_s",
        _rate(F.col("barge_ins"), F.col("turns")).alias("barge_in_rate"),
        "llm_ttft_p50_ms", "llm_ttft_p95_ms", "turn_p50_ms", "turn_p95_ms",
        "tool_calls", _rate(F.col("tool_ok"), F.col("tool_calls")).alias("tool_success_rate"),
        _rate(F.col("slots_rejected"), F.col("slots_filled") + F.col("slots_rejected"))
        .alias("slot_rejection_rate"),
        "errors",
        # Counts behind two of the rates, for the Insights page.
        F.coalesce("barge_ins", F.lit(0)).cast("long").alias("barge_ins"),
        F.coalesce("slots_rejected", F.lit(0)).cast("long").alias("slots_rejected"),
        F.current_timestamp().alias("computed_at"),
    )


def _columns(spark: SparkSession, path: str) -> set[str] | None:
    """The table's columns, or None when there is no Delta table there yet."""
    try:
        return set(spark.read.format("delta").load(path).columns)
    except Exception:       # noqa: BLE001 — not a Delta table yet
        return None


def write_days(spark: SparkSession, build: Callable[[str | None], DataFrame], path: str,
               since: str | None, partition: str = "call_date") -> None:
    """Write build(since) over the days >= since, atomically (Delta replaceWhere).

    The whole table is rebuilt instead, from build(None), when there is no
    `since`, when the table does not exist yet, or when its columns differ from
    what the code produces now: a column added to a gold table then exists for
    every day, not only for the days rebuilt since the change.
    """
    existing = _columns(spark, path)
    out = build(since)
    if since and existing is not None and existing == set(out.columns):
        (out.write.format("delta").mode("overwrite").partitionBy(partition)
         .option("replaceWhere", f"{partition} >= '{since}'").save(path))
        return
    (build(None).write.format("delta").mode("overwrite").partitionBy(partition)
     .option("overwriteSchema", "true").save(path))


def run(spark: SparkSession, since: str | None) -> dict:
    def build(since: str | None) -> DataFrame:
        calls = spark.read.format("delta").load(CALLS)
        turns = spark.read.format("delta").load(TURNS)
        if since:
            calls = calls.where(F.col("call_date") >= F.lit(since).cast("date"))
        return tenant_daily(calls, turns)

    # Replace exactly the recomputed days, atomically; older days untouched.
    write_days(spark, build, GOLD, since)
    return {"gold_rows": spark.read.format("delta").load(GOLD).count()}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="rebuild days on or after this date (YYYY-MM-DD); "
                                    "default: rebuild the whole table")
    args = ap.parse_args()
    print(run(connect(), args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
