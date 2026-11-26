"""Gold for the tenant Insights page: silver -> one small Delta table per section.

    SPARK_REMOTE=sc://localhost:15002 .venv/bin/python data/spark/jobs/gold_insights.py
    ... gold_insights.py --since 2026-11-22     # rebuild those days only

Each table is exactly what one part of the page shows, already aggregated, so
data/serving/publish.py copies rows into Postgres without computing anything:

  gold/sentiment_daily       utterances per emotion
  gold/call_sentiment_daily  calls with a sentiment summary: risk, trend, ended upset
  gold/tool_daily            tool calls per tool: ok, errors, timeouts, latency
  gold/knowledge_daily       document searches: covered, uncovered, failed
  gold/document_usage        how many searches each document answered
  gold/knowledge_gaps        questions no document covered, normalised and counted
  gold/hourly                calls per hour of the day (UTC)
  gold/calls_to_review       escalated calls and calls that ended upset, with
                             their (masked) transcript

Every row is keyed by tenant, the CALL's day and `synthetic`, like
gold/tenant_daily: simulator traffic and real callers never share a row.
Written like gold/tenant_daily too: whole days replaced atomically (Delta
replaceWhere), so a re-run gives the same table. A table whose silver source
does not exist yet (no platform call has searched documents, say) is skipped,
not written empty: there is nothing to say about it yet.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F

try:                    # run as a script: data/spark/jobs is on sys.path
    from bronze_call_events import LAKE, connect
    from gold_tenant_daily import write_days
    from silver_calls import CALL_EVENTS, CALL_SENTIMENT, CALLS, KNOWLEDGE_SEARCHES, TURNS, UTTERANCE_EMOTIONS, _exists
except ImportError:     # imported by Airflow as jobs.gold_insights
    from jobs.bronze_call_events import LAKE, connect
    from jobs.gold_tenant_daily import write_days
    from jobs.silver_calls import (
        CALL_EVENTS,
        CALL_SENTIMENT,
        CALLS,
        KNOWLEDGE_SEARCHES,
        TURNS,
        UTTERANCE_EMOTIONS,
        _exists,
    )

GOLD = f"{LAKE}/gold"
KEY = ["tenant_id", "call_date", "synthetic"]
# services/sentiment/app/emotions.py NEGATIVE_HIGH: a call that ENDS on one of
# these ended upset.
NEGATIVE_END = ["angry", "frustrated"]


def _keyed(df: DataFrame) -> DataFrame:
    """A call without CallStarted has synthetic NULL; count it as real traffic."""
    return df.withColumn("synthetic", F.coalesce("synthetic", F.lit(False)))


def _count_if(cond: Column) -> Column:
    return F.sum(F.when(cond, 1).otherwise(0)).cast("long")


def normalise_question(q: Column) -> Column:
    """trim, lowercase, collapse whitespace, strip trailing punctuation.

    The same rule as data/serving/publish.py normalise_question(), which applies
    it again before publishing (so the Postgres key holds whatever Spark did).
    """
    q = F.regexp_replace(F.lower(q), r"\s+", " ")
    q = F.regexp_replace(F.trim(q), r"[\s.,;:!?…]+$", "")
    return F.trim(q)


# -- the tables ------------------------------------------------------------------------

def sentiment_daily(utterances: DataFrame) -> DataFrame:
    return (_keyed(utterances).groupBy(*KEY, "emotion")
            .agg(F.count("*").alias("utterances")))


def call_sentiment_daily(summaries: DataFrame) -> DataFrame:
    s = _keyed(summaries)
    return s.groupBy(*KEY).agg(
        F.count("*").alias("calls"),
        _count_if(F.coalesce("escalation_risk", F.lit(False))).alias("escalation_risk_calls"),
        _count_if(F.col("trend") == "improving").alias("improving"),
        _count_if(F.col("trend") == "stable").alias("stable"),
        _count_if(F.col("trend") == "worsening").alias("worsening"),
        _count_if(F.col("last_emotion").isin(NEGATIVE_END)).alias("ended_negative"),
    )


def tool_daily(turns: DataFrame, calls: DataFrame) -> DataFrame:
    """ok + errors + timeouts = calls. A timeout counts as a timeout only."""
    t = (turns.where(F.col("tool").isNotNull()).drop("tenant_id")
         .join(_keyed(calls).select("call_id", *KEY), "call_id"))
    timed_out = F.coalesce("tool_timed_out", F.lit(False))
    error = F.coalesce("tool_outcome", F.lit("")) == "error"
    return t.groupBy(*KEY, "tool").agg(
        F.count("*").alias("calls"),
        _count_if(~timed_out & ~error).alias("ok"),
        _count_if(~timed_out & error).alias("errors"),
        _count_if(timed_out).alias("timeouts"),
        F.avg("tool_latency_ms").alias("avg_latency_ms"),
    )


def knowledge_daily(searches: DataFrame) -> DataFrame:
    """covered + uncovered + failed = searches. A failed search covers nothing."""
    failed = F.coalesce("failed", F.lit(False))
    covered = F.coalesce("covered", F.lit(False))
    return _keyed(searches).groupBy(*KEY).agg(
        F.count("*").alias("searches"),
        _count_if(covered & ~failed).alias("covered"),
        _count_if(~covered & ~failed).alias("uncovered"),
        _count_if(failed).alias("failed"),
        F.avg("latency_ms").alias("avg_latency_ms"),
    )


def document_usage(searches: DataFrame) -> DataFrame:
    """Searches each document contributed passages to (once per search)."""
    used = (_keyed(searches).where(~F.coalesce("failed", F.lit(False)))
            .select(*KEY, F.explode(F.array_distinct("document_titles")).alias("document_title"))
            .where(F.trim("document_title") != ""))
    return used.groupBy(*KEY, "document_title").agg(F.count("*").alias("uses"))


def knowledge_gaps(searches: DataFrame) -> DataFrame:
    """What callers asked that no document covered. Failed searches are outages, not gaps."""
    gaps = (_keyed(searches)
            .where(~F.coalesce("covered", F.lit(False)) & ~F.coalesce("failed", F.lit(False)))
            .withColumn("question", normalise_question(F.col("query")))
            .where(F.col("question").isNotNull() & (F.col("question") != "")))
    return gaps.groupBy(*KEY, "question").agg(
        F.count("*").alias("asked"),
        # How close the documents came at best: just under the threshold means
        # a document nearly covers it, near 0 means nothing is about it.
        F.max("best_similarity").alias("best_similarity"),
    )


def hourly(calls: DataFrame) -> DataFrame:
    started = F.coalesce("started_at", "first_event_at")      # session time zone: UTC
    return (_keyed(calls).withColumn("hour", F.hour(started))
            .groupBy(*KEY, "hour").agg(F.count("*").alias("calls")))


def calls_to_review(calls: DataFrame, events: DataFrame, summaries: DataFrame | None,
                    utterances: DataFrame | None) -> DataFrame:
    """Calls a human should look at: escalated, or ended on an upset caller.

    last_emotion is the call summary's, or else the latest classified
    utterance's (a call whose summary has not arrived yet). The transcript is
    every caller utterance and agent reply, masked in silver, as an array of
    (at, rank, seq, who, text) sorted by time; publish.py turns it into the
    page's [{who, text}].
    """
    c = _keyed(calls)
    last = F.lit(None).cast("string")
    if summaries is not None:
        c = c.join(summaries.select("call_id", F.col("last_emotion").alias("_summary_last")),
                   "call_id", "left")
        last = F.col("_summary_last")
    if utterances is not None:
        latest = (utterances.groupBy("call_id")
                  .agg(F.max_by("emotion", F.struct("classified_at", "utterance_index"))
                       .alias("_utterance_last")))
        c = c.join(latest, "call_id", "left")
        last = F.coalesce(last, F.col("_utterance_last"))
    c = c.withColumn("last_emotion", last)

    escalated = F.coalesce("escalated", F.lit(False))
    negative = F.coalesce(F.col("last_emotion").isin(NEGATIVE_END), F.lit(False))
    flagged = (c.where(escalated | negative)
               .withColumn("reason", F.concat_ws(",", F.when(escalated, F.lit("escalated")),
                                                 F.when(negative, F.lit("negative_end")))))

    is_caller = F.col("event_type") == "utterance_transcribed"
    lines = (events.join(flagged.select("call_id"), "call_id", "left_semi")
             .where(F.col("event_type").isin("utterance_transcribed", "turn_completed"))
             .select("call_id",
                     F.col("occurred_at").alias("at"),
                     # Same millisecond: the caller spoke before the agent answered.
                     F.when(is_caller, 0).otherwise(1).alias("rank"),
                     F.col("seq").cast("long").alias("seq"),
                     F.when(is_caller, "caller").otherwise("agent").alias("who"),
                     F.when(is_caller, F.col("payload.utterance_transcribed.text"))
                     .otherwise(F.col("payload.turn_completed.spoken_text")).alias("text"),
                     F.when(is_caller, F.col("payload.utterance_transcribed.ignored"))
                     .otherwise(F.lit(False)).alias("ignored"))
             .where(~F.coalesce("ignored", F.lit(False))
                    & F.col("text").isNotNull() & (F.trim("text") != "")))
    transcripts = lines.groupBy("call_id").agg(
        F.sort_array(F.collect_list(F.struct("at", "rank", "seq", "who", "text")))
        .alias("transcript"))

    return (flagged.join(transcripts, "call_id", "left")
            .select(*KEY, "call_id",
                    F.coalesce("started_at", "first_event_at").alias("started_at"),
                    "reason", "last_emotion",
                    (F.col("duration_ms") / 1000).alias("duration_s"),
                    F.col("turns").cast("long").alias("turns"),
                    "transcript"))


# -- the run ---------------------------------------------------------------------------

SOURCES = {
    "calls": (CALLS, "call_date"),
    "turns": (TURNS, None),                  # dated by their call, through the join
    "events": (CALL_EVENTS, "event_date"),
    "utterances": (UTTERANCE_EMOTIONS, "call_date"),
    "summaries": (CALL_SENTIMENT, "call_date"),
    "searches": (KNOWLEDGE_SEARCHES, "call_date"),
}

# name -> (required sources, optional sources, builder over those sources by name)
TABLES: dict[str, tuple[list[str], list[str], Callable[..., DataFrame]]] = {
    "sentiment_daily": (["utterances"], [], sentiment_daily),
    "call_sentiment_daily": (["summaries"], [], call_sentiment_daily),
    "tool_daily": (["turns", "calls"], [], tool_daily),
    "knowledge_daily": (["searches"], [], knowledge_daily),
    "document_usage": (["searches"], [], document_usage),
    "knowledge_gaps": (["searches"], [], knowledge_gaps),
    "hourly": (["calls"], [], hourly),
    "calls_to_review": (["calls", "events"], ["summaries", "utterances"], calls_to_review),
}


def _source(spark: SparkSession, name: str, since: str | None) -> DataFrame:
    path, partition = SOURCES[name]
    df = spark.read.format("delta").load(path)
    if since and partition:
        start = F.lit(since).cast("date")
        if name == "events":
            # A call's events can carry the day before its call_date (skewed
            # producer clocks); read one more day of them.
            start = F.date_sub(start, 1)
        df = df.where(F.col(partition) >= start)
    return df


def run(spark: SparkSession, since: str | None, only: list[str] | None = None) -> dict:
    present = {name for name, (path, _) in SOURCES.items() if _exists(spark, path)}
    stats: dict = {}
    for table, (required, optional, builder) in TABLES.items():
        if only and table not in only:
            continue
        if missing := [s for s in required if s not in present]:
            stats[table] = f"skipped: no silver {', '.join(missing)} yet"
            continue

        def build(since: str | None, required=required, optional=optional, builder=builder):
            args = [_source(spark, s, since) for s in required]
            args += [_source(spark, s, since) if s in present else None for s in optional]
            return builder(*args)

        path = f"{GOLD}/{table}"
        write_days(spark, build, path, since)
        stats[table] = spark.read.format("delta").load(path).count()
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="rebuild days on or after this date (YYYY-MM-DD); "
                                    "default: rebuild every table whole")
    ap.add_argument("tables", nargs="*", metavar="TABLE",
                    help=f"any of: {', '.join(TABLES)} (default: all)")
    args = ap.parse_args()
    if unknown := set(args.tables) - set(TABLES):
        ap.error(f"unknown table(s): {', '.join(sorted(unknown))}")
    print(run(connect(), args.since, args.tables or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
