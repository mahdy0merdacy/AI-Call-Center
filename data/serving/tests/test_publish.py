"""publish.py without a lake or a database: the pure parts, and the SQL it sends.

    .venv-quality/bin/python -m pytest data/serving/tests -q
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import publish
from publish import (
    DAILY,
    REVIEW_COLUMNS,
    aggregate_gaps,
    assemble_transcript,
    ddl,
    latest_per_tenant,
    normalise_question,
    to_rows,
)

DAY = date(2026, 11, 25)


# -- gap questions ----------------------------------------------------------------------

@pytest.mark.parametrize("raw, normalised", [
    ("  What are your   OPENING hours?? ", "what are your opening hours"),
    ("what are your opening hours", "what are your opening hours"),
    ("Do you deliver on Sundays ?", "do you deliver on sundays"),
    ("Refund\tpolicy...", "refund policy"),
    ("where is parcel [code]?", "where is parcel [code]"),     # masks are kept
    ("Hours?!…", "hours"),
    ("?!", ""),
    (None, ""),
    (float("nan"), ""),
])
def test_normalise_question(raw, normalised):
    assert normalise_question(raw) == normalised


def test_normalising_twice_changes_nothing():
    q = normalise_question("  Is it OPEN on Monday ?  ")
    assert normalise_question(q) == q


def test_gaps_that_normalise_alike_become_one_row():
    df = pd.DataFrame({
        "tenant_id": "t", "call_date": DAY, "synthetic": False,
        "question": ["opening hours?", "Opening  hours", "refunds", "  ...  "],
        "asked": [2, 3, 1, 4], "best_similarity": [0.2, 0.35, 0.1, 0.0]})
    out = aggregate_gaps(df).sort_values("question").reset_index(drop=True)
    assert list(out["question"]) == ["opening hours", "refunds"]
    assert list(out["asked"]) == [5, 1]
    assert list(out["best_similarity"]) == [0.35, 0.1]


def test_gaps_keep_tenants_days_and_traffic_apart():
    df = pd.DataFrame({
        "tenant_id": ["a", "b", "a"], "call_date": [DAY, DAY, DAY],
        "synthetic": [False, False, True], "question": ["hours"] * 3,
        "asked": [1, 1, 1], "best_similarity": [0.1, 0.1, 0.1]})
    assert len(aggregate_gaps(df)) == 3


def test_no_gaps():
    empty = pd.DataFrame(columns=["tenant_id", "call_date", "synthetic", "question", "asked",
                                  "best_similarity"])
    assert aggregate_gaps(empty).empty


# -- transcripts --------------------------------------------------------------------------

def _line(minute, who, text, seq=1, rank=None):
    return {"at": pd.Timestamp(datetime(2026, 11, 25, 9, minute, tzinfo=UTC)),
            "rank": rank if rank is not None else (0 if who == "caller" else 1),
            "seq": seq, "who": who, "text": text}


def test_a_transcript_is_ordered_and_trimmed():
    lines = np.array([
        _line(2, "agent", "Let me check that for you."),
        _line(0, "agent", "Hello, how can I help?"),
        _line(1, "caller", "  My parcel [code] is late. "),
        _line(3, "caller", "   "),                        # nothing said: dropped
    ], dtype=object)
    assert assemble_transcript(lines) == [
        {"who": "agent", "text": "Hello, how can I help?"},
        {"who": "caller", "text": "My parcel [code] is late."},
        {"who": "agent", "text": "Let me check that for you."},
    ]


def test_on_the_same_millisecond_the_caller_spoke_first():
    lines = [_line(1, "agent", "Sure."), _line(1, "caller", "Can you help?")]
    assert [x["who"] for x in assemble_transcript(lines)] == ["caller", "agent"]


def test_lines_of_one_speaker_keep_their_seq_order():
    lines = [_line(1, "agent", "second", seq=2), _line(1, "agent", "first", seq=1)]
    assert [x["text"] for x in assemble_transcript(lines)] == ["first", "second"]


@pytest.mark.parametrize("missing", [None, float("nan"), []])
def test_a_call_without_a_transcript(missing):
    assert assemble_transcript(missing) == []


# -- calls to review ------------------------------------------------------------------------

def review_gold(n: int, tenant: str = "t", synthetic: bool = False) -> pd.DataFrame:
    return pd.DataFrame({
        "tenant_id": tenant, "call_date": DAY, "synthetic": synthetic,
        "call_id": [f"{tenant}{synthetic}{i:03d}" for i in range(n)],
        "started_at": [pd.Timestamp("2026-11-25 08:00", tz="UTC") + pd.Timedelta(minutes=i)
                       for i in range(n)],
        "reason": "escalated", "last_emotion": None, "duration_s": 60.0, "turns": 3,
        "transcript": [[_line(0, "agent", "Hello.")]] * n})


def test_the_newest_50_per_tenant_and_traffic_type():
    df = pd.concat([review_gold(70), review_gold(70, synthetic=True), review_gold(10, "u")])
    out = latest_per_tenant(df)
    assert out.groupby(["tenant_id", "synthetic"]).size().to_dict() == {
        ("t", False): 50, ("t", True): 50, ("u", False): 10}
    kept = out[(out.tenant_id == "t") & ~out.synthetic]
    assert kept["started_at"].min() == pd.Timestamp("2026-11-25 08:20", tz="UTC")


def test_review_rows_match_the_contract():
    rows = to_rows(publish.review_frame(review_gold(1)), REVIEW_COLUMNS)
    (tenant, call, started, synthetic, reason, last, duration, turns, transcript), = rows
    assert (tenant, call, synthetic, reason, last, duration, turns) == (
        "t", "tFalse000", False, "escalated", None, 60.0, 3)
    assert started == datetime(2026, 11, 25, 8, tzinfo=UTC)
    assert transcript.obj == [{"who": "agent", "text": "Hello."}]


# -- rows for Postgres --------------------------------------------------------------------

def test_daily_rows_rename_the_day_and_become_plain_python():
    _, columns, _ = DAILY["tenant_daily"]
    df = pd.DataFrame({
        "tenant_id": ["acme"], "call_date": [DAY], "synthetic": [np.bool_(True)],
        "calls": [np.int64(12)], "ended_calls": [11], "escalated_calls": [1],
        "self_service_rate": [np.float64(10 / 11)], "escalation_rate": [1 / 11],
        "avg_duration_s": [61.2], "avg_turns": [4.5],
        "tool_calls": [np.nan],                           # no turns that day
        "tool_success_rate": [np.nan], "turn_p50_ms": [None], "turn_p95_ms": [np.nan],
        "barge_ins": [2], "errors": [0], "slots_rejected": [np.nan],
        "llm_ttft_p50_ms": [300], "computed_at": [pd.Timestamp.now()]})   # not published
    (row,) = to_rows(df, columns)
    assert row[:4] == ("acme", DAY, True, 12)
    as_dict = dict(zip([c for c, _ in columns], row, strict=True))
    assert as_dict["tool_calls"] == 0 and as_dict["slots_rejected"] == 0   # counts: 0
    assert as_dict["turn_p50_ms"] is None and as_dict["tool_success_rate"] is None
    assert all(type(v) in (str, int, float, bool, date, type(None)) for v in row)


def test_a_timestamp_day_becomes_a_date():
    _, columns, _ = DAILY["hourly"]
    df = pd.DataFrame({"tenant_id": ["a"], "call_date": [pd.Timestamp("2026-11-25")],
                       "synthetic": [None], "hour": [np.int32(9)], "calls": [3]})
    assert to_rows(df, columns) == [("a", DAY, False, 9, 3)]


def test_gold_missing_a_contract_column_fails_loudly():
    _, columns, _ = DAILY["tool_daily"]
    df = pd.DataFrame({"tenant_id": ["a"], "call_date": [DAY], "synthetic": [False],
                       "tool": ["x"], "calls": [1]})
    with pytest.raises(ValueError, match="ok"):
        to_rows(df, columns)


# -- DDL: the contract the API and the portal are built against ---------------------------

def test_ddl_has_every_table_and_key_of_the_contract():
    sql = "\n".join(ddl())
    for table, pk in {
        "tenant_daily": "tenant_id, day, synthetic",
        "sentiment_daily": "tenant_id, day, synthetic, emotion",
        "call_sentiment_daily": "tenant_id, day, synthetic",
        "tool_daily": "tenant_id, day, synthetic, tool",
        "knowledge_daily": "tenant_id, day, synthetic",
        "document_usage": "tenant_id, day, synthetic, document_title",
        "knowledge_gaps": "tenant_id, day, synthetic, question",
        "hourly": "tenant_id, day, synthetic, hour",
        "calls_to_review": "tenant_id, call_id",
        "tenant_links": "portal_tenant_id, data_tenant_id",
        "publish_runs": "run_id",
    }.items():
        block = sql.split(f"CREATE TABLE IF NOT EXISTS analytics.{table} (")[1].split(")\n")[0]
        assert f"PRIMARY KEY ({pk})" in sql.split(f"analytics.{table} (")[1].split("CREATE")[0]
        assert "tenant_id text" in block or table in ("tenant_links", "publish_runs")
    assert "portal_tenant_id uuid NOT NULL" in sql and "transcript jsonb" in sql
    assert "started_at timestamptz" in sql and "tables jsonb" in sql


# -- the transaction -----------------------------------------------------------------------

class FakeConn:
    """Records SQL; executemany through cursor()."""

    def __init__(self):
        self.sql: list[tuple[str, object]] = []
        self.in_tx = False

    @contextmanager
    def transaction(self):
        self.in_tx = True
        self.sql.append(("BEGIN", None))
        yield
        self.sql.append(("COMMIT", None))
        self.in_tx = False

    def execute(self, q, params=None):
        assert self.in_tx, f"outside the transaction: {q}"
        self.sql.append((" ".join(q.split()), params))
        return self

    @contextmanager
    def cursor(self):
        conn = self

        class Cur:
            def executemany(self, q, rows):
                assert conn.in_tx
                conn.sql.append((" ".join(q.split()), list(rows)))
        yield Cur()


def test_publish_replaces_the_days_in_one_transaction():
    frames = {t: None for t in [*DAILY, "calls_to_review"]}
    frames["hourly"] = pd.DataFrame({"tenant_id": ["a"], "call_date": [DAY], "synthetic": [False],
                                     "hour": [9], "calls": [3]})
    frames["knowledge_gaps"] = pd.DataFrame({
        "tenant_id": ["a", "a"], "call_date": [DAY, DAY], "synthetic": [False, False],
        "question": ["Hours?", "hours"], "asked": [1, 2], "best_similarity": [0.1, 0.2]})
    frames["calls_to_review"] = review_gold(2)
    since = {t: DAY for t in frames} | {"knowledge_gaps": None}
    conn = FakeConn()
    out = publish.publish(conn, frames, since, "run-1")

    assert conn.sql[0][0] == "BEGIN" and conn.sql[-1][0] == "COMMIT"
    assert [s for s, _ in conn.sql].count("BEGIN") == 1
    statements = [s for s, _ in conn.sql]
    assert ("DELETE FROM analytics.hourly WHERE day >= %s", (DAY,)) in conn.sql
    assert "DELETE FROM analytics.knowledge_gaps" in statements        # since None: all days
    assert not any("analytics.tool_daily" in s for s in statements)    # no gold: untouched
    gaps = next(rows for s, rows in conn.sql if s.startswith("INSERT INTO analytics.knowledge_gaps"))
    assert gaps == [("a", DAY, False, "hours", 3, 0.2)]
    assert any(s.startswith("DELETE FROM analytics.calls_to_review c USING") for s in statements)
    assert out["hourly"] == 1 and out["calls_to_review"] == 2
    assert out["tool_daily"] == "skipped: no gold table"
    run = next(p for s, p in conn.sql if s.startswith("INSERT INTO analytics.publish_runs"))
    assert run[0] == "run-1" and run[1].obj["hourly"] == 1
