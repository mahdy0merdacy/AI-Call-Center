"""Reading the lake for quality checks: Delta tables -> pandas, no Spark.

delta-rs reads the Delta log and the Parquet files straight from SeaweedFS, so
the checks need neither the Spark server nor a JVM: a few hundred MB of Python
in their own virtualenv, runnable from a laptop, an Airflow task or a CI job.
That is fine at this volume (thousands of rows a day). At Databricks volume the
same suites run on a Spark DataFrame instead; only this file changes.

Each loader returns the table as the checks need it: nested payload fields
flattened to columns, plus a few DERIVED columns (age of an open call, all
free text in one column). Checks stay one-liners against plain columns, and the
derivations live here, tested, instead of hidden inside expectation configs.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import pandas as pd
import pyarrow as pa
import yaml
from deltalake import DeltaTable

ROOT = Path(__file__).resolve().parents[2]
LAKE = os.environ.get("LAKE_URI", "s3://lake")
# Repo layout by default; the Airflow container mounts these elsewhere and says
# so with the same variables the Spark jobs and the orchestrator read.
GEN = Path(os.environ.get("AICC_GEN") or ROOT / "data" / "gen")
TENANTS = Path(os.environ.get("TENANTS_DIR") or ROOT / "tenants")


def storage_options() -> dict[str, str]:
    """SeaweedFS's S3 API, same credentials Spark uses (S3_ACCESS_KEY/S3_SECRET_KEY)."""
    return {
        "AWS_ENDPOINT_URL": os.environ.get("S3_ENDPOINT", "http://localhost:8333"),
        "AWS_ACCESS_KEY_ID": os.environ.get("S3_ACCESS_KEY", "lake"),
        "AWS_SECRET_ACCESS_KEY": os.environ["S3_SECRET_KEY"],
        "AWS_REGION": "us-east-1",
        "AWS_ALLOW_HTTP": "true",
    }


def read(table: str, partition: str | None = None, since: str | None = None) -> pa.Table:
    """A Delta table (e.g. 'silver/calls'), optionally only partitions >= since.

    The filter is on the partition column, so delta-rs skips whole files: a
    daily run reads one day, not the table's history.
    """
    dt = DeltaTable(f"{LAKE}/{table}", storage_options=storage_options())
    filters = [(partition, ">=", since)] if partition and since else None
    return dt.to_pyarrow_table(partitions=filters) if filters else dt.to_pyarrow_table()


def flatten(t: pa.Table) -> pd.DataFrame:
    """Nested structs -> dotted columns (payload.turn_completed.spoken_text)."""
    while any(pa.types.is_struct(f.type) for f in t.schema):
        t = t.flatten()
    return t.to_pandas()


# -- what the schema and the configs say is allowed ----------------------------------

@cache
def _descriptor():
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    fds = descriptor_pb2.FileDescriptorSet.FromString((GEN / "events.desc").read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for fd in fds.file:
        pool.Add(fd)
    pkg = "aicallcenter.events.v1"
    ext = pool.FindExtensionByName(f"{pkg}.pii")
    options_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName("google.protobuf.FieldOptions"))
    return pool, pkg, ext, options_cls


def event_types() -> set[str]:
    pool, pkg, *_ = _descriptor()
    event = pool.FindMessageTypeByName(f"{pkg}.Event")
    return {f.name for f in event.oneofs_by_name["payload"].fields}


def enum_names(enum: str, prefix: str) -> set[str]:
    """Enum values as silver writes them: END_REASON_ESCALATED -> 'escalated'."""
    pool, pkg, *_ = _descriptor()
    return {v.name.removeprefix(prefix).lower()
            for v in pool.FindEnumTypeByName(f"{pkg}.{enum}").values}


def free_text_columns() -> list[str]:
    """Flattened column names of every PII_FREE_TEXT field, from the (pii) options."""
    pool, pkg, ext, options_cls = _descriptor()
    free_text = pool.FindEnumTypeByName(f"{pkg}.Pii").values_by_name["PII_FREE_TEXT"].number
    event = pool.FindMessageTypeByName(f"{pkg}.Event")
    cols = []
    for member in event.oneofs_by_name["payload"].fields:
        for f in member.message_type.fields:
            opts = options_cls.FromString(f.GetOptions().SerializeToString())
            if opts.HasExtension(ext) and opts.Extensions[ext] == free_text:
                cols.append(f"payload.{member.name}.{f.name}")
    return cols


def yaml_tenants() -> set[str]:
    """The test tenants: tenants/*.yaml."""
    return {yaml.safe_load(p.read_text())["tenant_id"]
            for p in TENANTS.glob("*.yaml")}


@cache
def platform_tenants(url: str | None) -> frozenset[str]:
    """Active platform clients: accounts_tenant ids (UUIDs) in the portal's Postgres.

    Read as voice_reader, which may select only (id, name, slug, is_active) of
    accounts_tenant. No URL: none (a laptop or CI without the portal). A URL that
    does not answer raises: checking platform calls against the YAML alone would
    fail every one of them, so better to fail saying why.
    """
    if not url:
        return frozenset()
    import psycopg

    with psycopg.connect(url, connect_timeout=10) as conn:
        conn.read_only = True
        rows = conn.execute("SELECT id FROM accounts_tenant WHERE is_active").fetchall()
    return frozenset(str(r[0]) for r in rows)


def known_tenants() -> set[str]:
    """Every tenant a call may be for: the YAML test tenants, plus the active
    platform clients when PLATFORM_DATABASE_URL is set."""
    return yaml_tenants() | platform_tenants(os.environ.get("PLATFORM_DATABASE_URL"))


# What must never survive masking in free text (silver_calls.py masks these).
# Deliberately written independently of the masking regexes: a check that reuses
# the code it checks cannot catch that code's mistakes.
IDENTIFIER_PATTERN = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"                       # an email address
    r"|(?:\d[\s.\-]?){8,}\d"                         # 9+ digits: phone / account number
    r"|\b(?=\w*\d)(?=\w*[A-Za-z])[A-Za-z0-9]{8,12}\b"  # a code: AC4471X209
)


# -- loaders, one per checked table --------------------------------------------------

def bronze(since: str | None = None) -> pd.DataFrame:
    t = read("bronze/call_events", "ingest_date", since)
    t = t.select([c for c in t.column_names if c not in ("payload", "source")])
    return t.to_pandas()


def bronze_batch_stats(since: str | None = None) -> pd.DataFrame:
    """One row: table-level facts about the batch, so they can be checked like columns."""
    events = read("bronze/call_events", "ingest_date", since).num_rows
    try:
        quarantined = read("bronze/call_events_quarantine", "ingest_date", since).num_rows
    except Exception:   # noqa: BLE001 — no quarantine table until a bad record arrives
        quarantined = 0
    total = events + quarantined
    return pd.DataFrame([{"events": events, "quarantined": quarantined,
                          "quarantine_rate": quarantined / total if total else 0.0}])


def with_free_text(df: pd.DataFrame) -> pd.DataFrame:
    """All free text of an event in one column, so one check covers every field."""
    cols = [c for c in free_text_columns() if c in df.columns]
    df["free_text"] = (df[cols].fillna("").astype(str).agg(" ".join, axis=1).str.strip()
                       if cols else "")
    return df


def with_open_after_1h(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """A call still open an hour after its last event is not "in progress"."""
    now_ts = pd.Timestamp(now or datetime.now(UTC))
    last = pd.to_datetime(df["last_event_at"], utc=True)
    df["open_after_1h"] = (df["ends"] == 0) & ((now_ts - last) > pd.Timedelta(hours=1))
    return df


def silver_call_events(since: str | None = None) -> pd.DataFrame:
    return with_free_text(flatten(read("silver/call_events", "event_date", since)))


def silver_calls(since: str | None = None) -> pd.DataFrame:
    return with_open_after_1h(read("silver/calls", "call_date", since).to_pandas())


def silver_turns(since: str | None = None) -> pd.DataFrame:
    return read("silver/turns", "turn_date", since).to_pandas()


def gold_tenant_daily(since: str | None = None) -> pd.DataFrame:
    """Gold rows, each with the number of silver calls behind it (silver_calls).

    Reconciliation: a KPI table whose counts do not add up to its source is
    wrong however plausible its rates look.
    """
    gold = read("gold/tenant_daily", "call_date", since).to_pandas()
    calls = read("silver/calls", "call_date", since).to_pandas()
    calls["synthetic"] = calls["synthetic"].fillna(False).astype(bool)
    counts = (calls.groupby(["tenant_id", "call_date", "synthetic"]).size()
              .rename("silver_calls").reset_index())
    gold["synthetic"] = gold["synthetic"].astype(bool)
    return gold.merge(counts, on=["tenant_id", "call_date", "synthetic"], how="left")


# -- insights: silver sentiment and knowledge, gold for the Insights page --------------
#
# These tables appear only once their events do (no sentiment service, no
# platform call yet), so a missing table reads as an EMPTY frame with the columns
# its checks name: nothing to check is not a failure. Any other read error still
# raises.

KEY = ["tenant_id", "call_date", "synthetic"]


def read_or_empty(table: str, partition: str | None, since: str | None,
                  columns: list[str]) -> pd.DataFrame:
    from deltalake.exceptions import TableNotFoundError

    try:
        return read(table, partition, since).to_pandas()
    except TableNotFoundError:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


def _synthetic(df: pd.DataFrame) -> pd.DataFrame:
    """As gold keys it: a call without CallStarted (synthetic NULL) is real traffic."""
    df["synthetic"] = df["synthetic"].astype("boolean").fillna(False).astype(bool)
    return df


def _reconcile(gold: pd.DataFrame, silver: pd.DataFrame, keys: list[str],
               name: str) -> pd.DataFrame:
    """gold + column `name`: the silver rows behind each gold row (0 when none)."""
    gold = _synthetic(gold)
    if gold.empty:
        gold[name] = pd.Series(dtype="int64")
        return gold
    silver = _synthetic(silver)
    counts = silver.groupby(keys).size().rename(name).reset_index()
    out = gold.merge(counts, on=keys, how="left")
    out[name] = out[name].fillna(0).astype("int64")
    return out


SILVER_UTTERANCE_EMOTIONS = ["event_id", "call_id", "tenant_id", "call_date", "synthetic",
                             "utterance_index", "emotion", "score"]
SILVER_CALL_SENTIMENT = ["call_id", "tenant_id", "call_date", "synthetic", "utterances",
                         "dominant", "first_emotion", "last_emotion", "trend", "escalation_risk"]
SILVER_KNOWLEDGE_SEARCHES = ["event_id", "call_id", "tenant_id", "call_date", "synthetic",
                             "query", "hits", "best_similarity", "covered", "failed",
                             "document_titles", "latency_ms"]


def silver_utterance_emotions(since: str | None = None) -> pd.DataFrame:
    return read_or_empty("silver/utterance_emotions", "call_date", since,
                         SILVER_UTTERANCE_EMOTIONS)


def silver_call_sentiment(since: str | None = None) -> pd.DataFrame:
    return read_or_empty("silver/call_sentiment", "call_date", since, SILVER_CALL_SENTIMENT)


def silver_knowledge_searches(since: str | None = None) -> pd.DataFrame:
    return read_or_empty("silver/knowledge_searches", "call_date", since,
                         SILVER_KNOWLEDGE_SEARCHES)


def with_outcome_total(df: pd.DataFrame, parts: list[str]) -> pd.DataFrame:
    """The sum of the parts a total must split into (ok + errors + timeouts = calls)."""
    df["outcome_total"] = df[parts].fillna(0).sum(axis=1).astype("int64") if len(df) else 0
    return df


def gold_sentiment_daily(since: str | None = None) -> pd.DataFrame:
    gold = read_or_empty("gold/sentiment_daily", "call_date", since,
                         [*KEY, "emotion", "utterances"])
    return _reconcile(gold, silver_utterance_emotions(since), [*KEY, "emotion"],
                      "silver_utterances")


def gold_call_sentiment_daily(since: str | None = None) -> pd.DataFrame:
    gold = read_or_empty("gold/call_sentiment_daily", "call_date", since,
                         [*KEY, "calls", "escalation_risk_calls", "improving", "stable",
                          "worsening", "ended_negative"])
    gold = with_outcome_total(gold, ["improving", "stable", "worsening"])
    return _reconcile(gold, silver_call_sentiment(since), KEY, "silver_calls")


def gold_tool_daily(since: str | None = None) -> pd.DataFrame:
    gold = read_or_empty("gold/tool_daily", "call_date", since,
                         [*KEY, "tool", "calls", "ok", "errors", "timeouts", "avg_latency_ms"])
    gold = with_outcome_total(gold, ["ok", "errors", "timeouts"])
    # Turns carry the tool; the day and traffic type come from their call.
    turns = read("silver/turns").select(["call_id", "tool"]).to_pandas()
    calls = read("silver/calls", "call_date", since).select(
        ["call_id", *KEY]).to_pandas()
    tools = turns[turns["tool"].notna()].merge(calls, on="call_id")
    return _reconcile(gold, tools, [*KEY, "tool"], "silver_tool_calls")


def gold_knowledge_daily(since: str | None = None) -> pd.DataFrame:
    gold = read_or_empty("gold/knowledge_daily", "call_date", since,
                         [*KEY, "searches", "covered", "uncovered", "failed", "avg_latency_ms"])
    gold = with_outcome_total(gold, ["covered", "uncovered", "failed"])
    return _reconcile(gold, silver_knowledge_searches(since), KEY, "silver_searches")


def gold_document_usage(since: str | None = None) -> pd.DataFrame:
    return read_or_empty("gold/document_usage", "call_date", since,
                         [*KEY, "document_title", "uses"])


def gold_knowledge_gaps(since: str | None = None) -> pd.DataFrame:
    return read_or_empty("gold/knowledge_gaps", "call_date", since,
                         [*KEY, "question", "asked", "best_similarity"])


def with_day_calls(df: pd.DataFrame) -> pd.DataFrame:
    """Calls of the row's whole day (all hours), to reconcile with silver per day."""
    df["day_calls"] = (df.groupby(KEY)["calls"].transform("sum").astype("int64")
                       if len(df) else pd.Series(dtype="int64"))
    return df


def gold_hourly(since: str | None = None) -> pd.DataFrame:
    gold = read_or_empty("gold/hourly", "call_date", since, [*KEY, "hour", "calls"])
    calls = read("silver/calls", "call_date", since).select(KEY).to_pandas()
    return _reconcile(with_day_calls(_synthetic(gold)), calls, KEY, "silver_calls")


def with_transcript_text(df: pd.DataFrame) -> pd.DataFrame:
    """Every line of the transcript in one column, so one check covers them all."""
    def text(lines) -> str:
        if lines is None or isinstance(lines, float):      # NULL, or NaN in pandas
            return ""
        return " ".join(str(line.get("text") or "") for line in lines).strip()

    df["transcript_text"] = (df["transcript"].map(text) if len(df)
                             else pd.Series(dtype="object"))
    return df


def gold_calls_to_review(since: str | None = None) -> pd.DataFrame:
    return with_transcript_text(read_or_empty(
        "gold/calls_to_review", "call_date", since,
        [*KEY, "call_id", "started_at", "reason", "last_emotion", "duration_s", "turns",
         "transcript"]))

