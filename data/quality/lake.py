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


def known_tenants() -> set[str]:
    return {yaml.safe_load(p.read_text())["tenant_id"]
            for p in TENANTS.glob("*.yaml")}


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

