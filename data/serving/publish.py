"""Publish the Insights gold tables to Postgres, where the portal reads them.

    S3_SECRET_KEY=... ANALYTICS_DATABASE_URL=postgresql://aicc:aicc@localhost:5433/aicc \\
        .venv-quality/bin/python data/serving/publish.py --since 2026-11-25
    ... publish.py --full          # every day the lake has

Run by Airflow (task publish_insights) after gold_checks, so only gold that
passed its quality gate is ever published. Reads the gold Delta tables with
delta-rs (no Spark; same S3 settings as data/quality/lake.py) and writes the
`analytics` schema of the data Postgres:

  tenant_daily, sentiment_daily, call_sentiment_daily, tool_daily,
  knowledge_daily, document_usage, knowledge_gaps, hourly
      one row per (tenant, day, synthetic[, item]). The days >= --since are
      DELETEd and re-INSERTed, every table in ONE transaction: a reader sees
      the previous publish or this one, never half of each, and a re-run gives
      the same rows (gold recomputes those days whole, so it is the truth for them).
  calls_to_review
      the latest 50 flagged calls per tenant and traffic type (synthetic or
      not, so a load test cannot push real callers off the list), with their
      masked transcript as [{"who": "caller"|"agent", "text": ...}].
  tenant_links
      portal tenant id <-> data tenant id. Created empty here, filled elsewhere.
  publish_runs
      one row per publish: when, and how many rows of each table.

A table with nothing published yet (the first publish, a table added later,
gold that only now exists) gets everything gold has, not just the recent days.

Roles. Tables are created and written as `aicc`, which owns the schema. The
portal reads as `insights`: USAGE on analytics, SELECT on its tables, nothing
else. Creating that role needs a superuser (aicc cannot create roles), so it is
ensured through ANALYTICS_ADMIN_DATABASE_URL when that is set; init SQL does
the same for a fresh volume, but the running cluster's Postgres already exists.

Env:
  ANALYTICS_DATABASE_URL        postgresql://aicc:aicc@postgres:5432/aicc
  ANALYTICS_ADMIN_DATABASE_URL  optional, a superuser on the same database
  INSIGHTS_DB_PASSWORD          the insights role's password (default: insights)
  LAKE_URI, S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY   as data/quality/lake.py
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from uuid import uuid4

import pandas as pd

log = logging.getLogger("publish")

SCHEMA = "analytics"
LAKE = os.environ.get("LAKE_URI", "s3://lake")
REVIEW_LIMIT = 50
READER_ROLE = "insights"

# -- the contract: what the portal's API reads. Column order = insert order. ----------

KEY = [("tenant_id", "text"), ("day", "date"), ("synthetic", "boolean")]

# name -> (gold table, columns, primary key). Every daily table's key starts with KEY.
DAILY: dict[str, tuple[str, list[tuple[str, str]], list[str]]] = {
    "tenant_daily": ("gold/tenant_daily", [
        *KEY, ("calls", "integer"), ("ended_calls", "integer"), ("escalated_calls", "integer"),
        ("self_service_rate", "real"), ("escalation_rate", "real"),
        ("avg_duration_s", "real"), ("avg_turns", "real"),
        ("tool_calls", "integer"), ("tool_success_rate", "real"),
        ("turn_p50_ms", "integer"), ("turn_p95_ms", "integer"),
        ("barge_ins", "integer"), ("errors", "integer"), ("slots_rejected", "integer"),
    ], []),
    "sentiment_daily": ("gold/sentiment_daily", [
        *KEY, ("emotion", "text"), ("utterances", "integer"),
    ], ["emotion"]),
    "call_sentiment_daily": ("gold/call_sentiment_daily", [
        *KEY, ("calls", "integer"), ("escalation_risk_calls", "integer"),
        ("improving", "integer"), ("stable", "integer"), ("worsening", "integer"),
        ("ended_negative", "integer"),
    ], []),
    "tool_daily": ("gold/tool_daily", [
        *KEY, ("tool", "text"), ("calls", "integer"), ("ok", "integer"), ("errors", "integer"),
        ("timeouts", "integer"), ("avg_latency_ms", "real"),
    ], ["tool"]),
    "knowledge_daily": ("gold/knowledge_daily", [
        *KEY, ("searches", "integer"), ("covered", "integer"), ("uncovered", "integer"),
        ("failed", "integer"), ("avg_latency_ms", "real"),
    ], []),
    "document_usage": ("gold/document_usage", [
        *KEY, ("document_title", "text"), ("uses", "integer"),
    ], ["document_title"]),
    "knowledge_gaps": ("gold/knowledge_gaps", [
        *KEY, ("question", "text"), ("asked", "integer"), ("best_similarity", "real"),
    ], ["question"]),
    "hourly": ("gold/hourly", [
        *KEY, ("hour", "integer"), ("calls", "integer"),
    ], ["hour"]),
}

REVIEW_GOLD = "gold/calls_to_review"
REVIEW_COLUMNS = [
    ("tenant_id", "text"), ("call_id", "text"), ("started_at", "timestamptz"),
    ("synthetic", "boolean"), ("reason", "text"), ("last_emotion", "text"),
    ("duration_s", "real"), ("turns", "integer"), ("transcript", "jsonb"),
]

# Integers that are not counts: NULL means "not measured", not 0.
NULLABLE_INTS = {"turn_p50_ms", "turn_p95_ms", "turns"}


def ddl() -> list[str]:
    """CREATE ... IF NOT EXISTS for every table of the contract."""
    def table(name: str, columns: list[tuple[str, str]], pk: list[str]) -> str:
        cols = ",\n  ".join(f"{c} {t}{' NOT NULL' if c in pk else ''}" for c, t in columns)
        return (f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{name} (\n  {cols},\n"
                f"  PRIMARY KEY ({', '.join(pk)})\n)")

    out = [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"]
    for name, (_, columns, extra) in DAILY.items():
        out.append(table(name, columns, [c for c, _ in KEY] + extra))
    out.append(table("calls_to_review", REVIEW_COLUMNS, ["tenant_id", "call_id"]))
    out.append(table("tenant_links", [("portal_tenant_id", "uuid"), ("data_tenant_id", "text")],
                     ["portal_tenant_id", "data_tenant_id"]))
    out.append(table("publish_runs", [("run_id", "text"), ("published_at", "timestamptz"),
                                      ("tables", "jsonb")], ["run_id"]))
    return out


TABLES = [*DAILY, "calls_to_review", "tenant_links", "publish_runs"]


# -- pure transformations (tested in data/serving/tests) -----------------------------

_TRAILING = re.compile(r"[\s.,;:!?…]+$")


def normalise_question(q: object) -> str:
    """trim, lowercase, collapse whitespace, strip trailing punctuation.

    Same rule as gold_insights.normalise_question (Spark). Applied again here so
    the Postgres key holds whatever Spark's regex dialect did: two questions
    that normalise alike become one row instead of a primary-key violation.
    """
    if q is None or (isinstance(q, float) and math.isnan(q)):
        return ""
    return _TRAILING.sub("", " ".join(str(q).split()).lower()).strip()


def aggregate_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Gaps re-keyed by the normalised question: asked summed, best similarity max."""
    keys = [c for c, _ in KEY] + ["question"]
    df = df.rename(columns={"call_date": "day"})
    if df.empty:
        return df.reindex(columns=[*keys, "asked", "best_similarity"])
    df = df.assign(question=df["question"].map(normalise_question))
    df = df[df["question"] != ""]
    return (df.groupby(keys, as_index=False, dropna=False)
            .agg(asked=("asked", "sum"), best_similarity=("best_similarity", "max")))


def _is_null(v: object) -> bool:
    if v is None or v is pd.NaT or v is pd.NA:
        return True
    return isinstance(v, float) and math.isnan(v)


def assemble_transcript(lines: Iterable | None) -> list[dict[str, str]]:
    """Gold's transcript lines -> [{"who", "text"}], in the order they were said.

    Gold sorts already; sorted again on (at, rank, seq) so the order never
    depends on how a reader returned an array. rank 0 = caller, 1 = agent: on
    the same millisecond the caller spoke first. Empty lines are dropped.
    """
    if lines is None or _is_null(lines):
        return []
    rows = [dict(line) for line in lines if line is not None]

    def order(line: dict):
        at = line.get("at")
        at = pd.Timestamp(at).value if not _is_null(at) else -1
        return (at, line.get("rank") if line.get("rank") is not None else 1,
                line.get("seq") if line.get("seq") is not None else 0)

    out = []
    for line in sorted(rows, key=order):
        text = str(line.get("text") or "").strip()
        if text:
            out.append({"who": "caller" if line.get("who") == "caller" else "agent",
                        "text": text})
    return out


def latest_per_tenant(df: pd.DataFrame, n: int = REVIEW_LIMIT) -> pd.DataFrame:
    """The n most recent rows per (tenant, synthetic), newest first."""
    if df.empty:
        return df
    df = df.sort_values(["started_at", "call_id"], ascending=False)
    return df.groupby(["tenant_id", "synthetic"], dropna=False).head(n)


def _value(v: object, pg_type: str, column: str):
    """A pandas/numpy value -> the Python value psycopg sends for pg_type."""
    if _is_null(v):
        if pg_type == "integer" and column not in NULLABLE_INTS:
            return 0            # a count with nothing to count
        if pg_type == "boolean" and column == "synthetic":
            return False        # as gold keys it: no CallStarted = real traffic
        return None
    if hasattr(v, "item") and not isinstance(v, (pd.Timestamp, list, dict)):
        v = v.item()            # numpy scalar -> Python
    if pg_type == "integer":
        return int(round(v))
    if pg_type == "real":
        return float(v)
    if pg_type == "boolean":
        return bool(v)
    if pg_type == "date":
        if isinstance(v, datetime | pd.Timestamp):
            return v.date()
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    if pg_type == "timestamptz":
        ts = pd.Timestamp(v)
        return (ts.tz_localize(UTC) if ts.tzinfo is None else ts).to_pydatetime()
    if pg_type == "jsonb":
        from psycopg.types.json import Jsonb
        return Jsonb(v)
    return str(v)


def to_rows(df: pd.DataFrame, columns: list[tuple[str, str]]) -> list[tuple]:
    """Gold frame -> tuples in contract column order (call_date is published as day)."""
    if "call_date" in df.columns and "day" not in df.columns:
        df = df.rename(columns={"call_date": "day"})
    names = [c for c, _ in columns]
    missing = [c for c in names if c not in df.columns]
    if missing and not df.empty:
        raise ValueError(f"gold is missing column(s) {missing}")
    return [tuple(_value(v, t, c) for v, (c, t) in zip(rec, columns, strict=True))
            for rec in df.reindex(columns=names).itertuples(index=False, name=None)]


def review_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Gold calls_to_review -> the published shape: newest 50, transcript assembled."""
    df = latest_per_tenant(df)
    return df.assign(transcript=[assemble_transcript(t) for t in df["transcript"]]
                     if not df.empty else pd.Series(dtype=object))


# -- the lake --------------------------------------------------------------------------

def storage_options() -> dict[str, str]:
    """SeaweedFS's S3 API, same settings as data/quality/lake.py."""
    return {
        "AWS_ENDPOINT_URL": os.environ.get("S3_ENDPOINT", "http://localhost:8333"),
        "AWS_ACCESS_KEY_ID": os.environ.get("S3_ACCESS_KEY", "lake"),
        "AWS_SECRET_ACCESS_KEY": os.environ["S3_SECRET_KEY"],
        "AWS_REGION": "us-east-1",
        "AWS_ALLOW_HTTP": "true",
    }


def read_gold(table: str, since: date | None) -> pd.DataFrame | None:
    """A gold table's days >= since, or None if gold has no such table yet."""
    from deltalake import DeltaTable
    from deltalake.exceptions import TableNotFoundError

    try:
        dt = DeltaTable(f"{LAKE}/{table}", storage_options=storage_options())
    except TableNotFoundError:
        return None
    filters = [("call_date", ">=", since.isoformat())] if since else None
    return dt.to_pyarrow_table(partitions=filters).to_pandas()


# -- Postgres --------------------------------------------------------------------------

def ensure_tables(conn) -> set[str]:
    """Create what is missing. Returns the tables that did not exist before."""
    existing = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (SCHEMA,)).fetchall()}
    for statement in ddl():
        conn.execute(statement)
    return set(TABLES) - existing


def ensure_reader_role(admin_url: str | None, password: str) -> None:
    """The `insights` login role, as a superuser. Idempotent; resets the password."""
    if not admin_url:
        return
    import psycopg
    from psycopg import sql

    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s",
                              (READER_ROLE,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(sql.SQL(
            "{} ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            "PASSWORD {}").format(sql.SQL(verb), sql.Identifier(READER_ROLE),
                                  sql.Literal(password)))


def grant_reader(conn) -> bool:
    """USAGE on the schema, SELECT on its tables, as their owner. False: no role yet."""
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER_ROLE,)).fetchone():
        log.warning("role %s does not exist: set ANALYTICS_ADMIN_DATABASE_URL to create it",
                    READER_ROLE)
        return False
    db = conn.execute("SELECT current_database()").fetchone()[0]
    conn.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {READER_ROLE}')
    conn.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {READER_ROLE}")
    conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {READER_ROLE}")
    conn.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT SELECT ON TABLES "
                 f"TO {READER_ROLE}")
    return True


def _insert(conn, table: str, columns: list[tuple[str, str]], rows: list[tuple],
            conflict: str = "") -> None:
    if not rows:
        return
    names = ", ".join(c for c, _ in columns)
    marks = ", ".join(["%s"] * len(columns))
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO {SCHEMA}.{table} ({names}) VALUES ({marks}){conflict}",
                        rows)


def publish(conn, frames: dict[str, pd.DataFrame | None], since: dict[str, date | None],
            run_id: str) -> dict[str, int | str]:
    """Replace the published days of every table, all in one transaction.

    frames: table -> gold rows (None: no gold table yet, leave Postgres as is).
    since:  table -> first day replaced (None: replace the whole table).
    """
    published: dict[str, int | str] = {}
    with conn.transaction():
        # One publisher at a time, even if two Airflow runs ever overlap.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('analytics.publish'))")
        for table, (_, columns, _) in DAILY.items():
            df = frames.get(table)
            if df is None:
                published[table] = "skipped: no gold table"
                continue
            if table == "knowledge_gaps":
                df = aggregate_gaps(df)
            rows = to_rows(df, columns)
            if since.get(table):
                conn.execute(f"DELETE FROM {SCHEMA}.{table} WHERE day >= %s", (since[table],))
            else:
                conn.execute(f"DELETE FROM {SCHEMA}.{table}")
            _insert(conn, table, columns, rows)
            published[table] = len(rows)

        df = frames.get("calls_to_review")
        if df is None:
            published["calls_to_review"] = "skipped: no gold table"
        else:
            rows = to_rows(review_frame(df), REVIEW_COLUMNS)
            start = since.get("calls_to_review")
            if start:
                conn.execute(f"DELETE FROM {SCHEMA}.calls_to_review WHERE started_at >= %s",
                             (datetime.combine(start, time(), UTC),))
            else:
                conn.execute(f"DELETE FROM {SCHEMA}.calls_to_review")
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c, _ in REVIEW_COLUMNS[2:])
            _insert(conn, "calls_to_review", REVIEW_COLUMNS, rows,
                    f" ON CONFLICT (tenant_id, call_id) DO UPDATE SET {updates}")
            # Older days stay as published; keep the newest 50 of old and new together.
            conn.execute(f"""
                DELETE FROM {SCHEMA}.calls_to_review c USING (
                    SELECT tenant_id, call_id, row_number() OVER (
                        PARTITION BY tenant_id, synthetic
                        ORDER BY started_at DESC, call_id DESC) AS n
                    FROM {SCHEMA}.calls_to_review) r
                WHERE c.tenant_id = r.tenant_id AND c.call_id = r.call_id AND r.n > %s""",
                         (REVIEW_LIMIT,))
            published["calls_to_review"] = len(rows)

        from psycopg.types.json import Jsonb
        conn.execute(f"""
            INSERT INTO {SCHEMA}.publish_runs (run_id, published_at, tables)
            VALUES (%s, now(), %s)
            ON CONFLICT (run_id) DO UPDATE SET published_at = now(), tables = EXCLUDED.tables""",
                     (run_id, Jsonb({**published, "since": {
                         t: d.isoformat() if d else None for t, d in since.items()}})))
    return published


def run(since: date | None, run_id: str, full: bool = False) -> dict:
    import psycopg

    url = os.environ.get("ANALYTICS_DATABASE_URL", "postgresql://aicc:aicc@localhost:5433/aicc")
    ensure_reader_role(os.environ.get("ANALYTICS_ADMIN_DATABASE_URL"),
                       os.environ.get("INSIGHTS_DB_PASSWORD", "insights"))
    # autocommit: the only transactions are the explicit transaction() blocks.
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.transaction():
            ensure_tables(conn)
            granted = grant_reader(conn)
            # A table with nothing published yet gets all of gold, not just the
            # recent days: the first publish, or gold that appeared only now.
            empty = {t for t in [*DAILY, "calls_to_review"] if not conn.execute(
                f"SELECT 1 FROM {SCHEMA}.{t} LIMIT 1").fetchone()}
        windows = {t: None if (full or t in empty) else since
                   for t in [*DAILY, "calls_to_review"]}
        frames = {t: read_gold(DAILY[t][0] if t in DAILY else REVIEW_GOLD, windows[t])
                  for t in windows}
        published = publish(conn, frames, windows, run_id)
    return {"run_id": run_id, "published": published, "reader_role_granted": granted}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=date.fromisoformat,
                    help="publish days on or after this date (YYYY-MM-DD)")
    ap.add_argument("--full", action="store_true", help="publish every day gold has")
    ap.add_argument("--run-id", default=None, help="publish_runs key (default: generated)")
    args = ap.parse_args()
    if not args.since and not args.full:
        ap.error("give --since YYYY-MM-DD or --full")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_id = args.run_id or f"manual__{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid4().hex[:6]}"
    print(json.dumps(run(args.since, run_id, args.full), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
