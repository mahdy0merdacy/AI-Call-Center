"""The Insights tables' rules, and which tenants count as known.

    .venv-quality/bin/python -m pytest data/quality/tests -q

Like test_quality.py: no lake, frames built here, the pipeline's own suites
and runner.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import great_expectations as gx
import lake
from run import check
from suites import EMOTIONS

N = 200
DAY = date(2026, 11, 25)
PLATFORM_TENANT = str(uuid.UUID("0b6f9f8e-4f6a-4c37-9a51-6f1b1f0d2a11"))


@pytest.fixture(scope="module")
def context():
    return gx.get_context(mode="ephemeral")


@pytest.fixture(autouse=True)
def no_platform(monkeypatch):
    """YAML tenants only, unless a test says otherwise."""
    monkeypatch.delenv("PLATFORM_DATABASE_URL", raising=False)
    lake.platform_tenants.cache_clear()


def _verdict(context, table: str, df: pd.DataFrame) -> dict[str, list[str]]:
    report = check(context, table, df, docs=False)
    return {sev: [f"{f['rule']}:{f['column']}" for f in s["failed"]]
            for sev, s in report["suites"].items()}


def _clean(context, table, df):
    assert _verdict(context, table, df) == {"critical": [], "warning": []}


# -- silver ------------------------------------------------------------------------------

def utterances(n: int = N) -> pd.DataFrame:
    return pd.DataFrame({
        "event_id": [f"E{i:04d}.emotion" for i in range(n)],
        "call_id": [f"C{i // 2:04d}" for i in range(n)], "utterance_index": [i % 2 for i in range(n)],
        "tenant_id": "acme-delivery", "call_date": DAY, "synthetic": True,
        "emotion": [EMOTIONS[i % len(EMOTIONS)] for i in range(n)], "score": 0.8,
    })


def call_sentiment(n: int = N) -> pd.DataFrame:
    return pd.DataFrame({
        "call_id": [f"C{i:04d}" for i in range(n)], "tenant_id": "acme-delivery",
        "call_date": DAY, "synthetic": True, "utterances": 3, "dominant": "neutral",
        "first_emotion": "confused", "last_emotion": "satisfied", "trend": "improving",
        "escalation_risk": False,
    })


def searches(n: int = N) -> pd.DataFrame:
    return pd.DataFrame({
        "event_id": [f"K{i:04d}" for i in range(n)], "call_id": [f"C{i:04d}" for i in range(n)],
        "tenant_id": "acme-delivery", "call_date": DAY, "synthetic": False,
        "query": "What are your opening hours for parcel [code]?", "hits": 3,
        "best_similarity": 0.62, "covered": True, "failed": False, "latency_ms": 80,
    })


def test_clean_silver_insights_pass(context):
    _clean(context, "silver_utterance_emotions", utterances())
    _clean(context, "silver_call_sentiment", call_sentiment())
    _clean(context, "silver_knowledge_searches", searches())


def test_an_unknown_emotion_only_warns(context):
    df = utterances()
    df.loc[0, "emotion"] = "bored"                     # a newer model's class
    v = _verdict(context, "silver_utterance_emotions", df)
    assert v["critical"] == [] and "expect_column_values_to_be_in_set:emotion" in v["warning"]


def test_an_emotion_row_without_its_call_is_critical(context):
    df = utterances()
    df.loc[0, "call_date"] = None
    assert "expect_column_values_to_not_be_null:call_date" in _verdict(
        context, "silver_utterance_emotions", df)["critical"]


def test_a_restarted_utterance_count_only_warns(context):
    df = utterances()
    df.loc[2, ["call_id", "utterance_index"]] = ["C0000", 0]   # same index, another event
    v = _verdict(context, "silver_utterance_emotions", df)
    assert v["critical"] == [] and any(r.startswith("expect_compound_columns_to_be_unique")
                                       for r in v["warning"])


def test_an_unknown_trend_is_critical(context):
    df = call_sentiment()
    df.loc[0, "trend"] = "sideways"
    assert "expect_column_values_to_be_in_set:trend" in _verdict(
        context, "silver_call_sentiment", df)["critical"]


@pytest.mark.parametrize("leak", ["is 0142887711 still active", "jane.doe@example.com",
                                  "where is AC4471X209"])
def test_an_identifier_in_a_search_query_is_critical(context, leak):
    df = searches()
    df.loc[0, "query"] = leak
    assert "expect_column_values_to_not_match_regex:query" in _verdict(
        context, "silver_knowledge_searches", df)["critical"]


def test_a_similarity_outside_the_contract_warns_outside_cosine_is_critical(context):
    df = searches()
    df.loc[0, "best_similarity"] = -0.1
    v = _verdict(context, "silver_knowledge_searches", df)
    assert v["critical"] == [] and "expect_column_values_to_be_between:best_similarity" in v["warning"]
    df.loc[0, "best_similarity"] = 3.0
    assert "expect_column_values_to_be_between:best_similarity" in _verdict(
        context, "silver_knowledge_searches", df)["critical"]


# -- gold ----------------------------------------------------------------------------------

def _days(n: int = 4) -> list[date]:
    return list(pd.date_range("2026-11-20", periods=n).date)


def gold_sentiment(n: int = 4) -> pd.DataFrame:
    rows = [{"tenant_id": "acme-delivery", "call_date": d, "synthetic": True, "emotion": e,
             "utterances": 5, "silver_utterances": 5} for d in _days(n) for e in EMOTIONS]
    return pd.DataFrame(rows)


def gold_call_sentiment(n: int = 4) -> pd.DataFrame:
    df = pd.DataFrame({
        "tenant_id": "acme-delivery", "call_date": _days(n), "synthetic": True, "calls": 10,
        "silver_calls": 10, "escalation_risk_calls": 2, "improving": 3, "stable": 5,
        "worsening": 2, "ended_negative": 1})
    return lake.with_outcome_total(df, ["improving", "stable", "worsening"])


def gold_tools(n: int = 4) -> pd.DataFrame:
    df = pd.DataFrame({
        "tenant_id": "acme-delivery", "call_date": _days(n), "synthetic": False,
        "tool": "lookup_shipment", "calls": 10, "silver_tool_calls": 10, "ok": 8, "errors": 1,
        "timeouts": 1, "avg_latency_ms": 120.0})
    return lake.with_outcome_total(df, ["ok", "errors", "timeouts"])


def gold_knowledge(n: int = 4) -> pd.DataFrame:
    df = pd.DataFrame({
        "tenant_id": PLATFORM_TENANT, "call_date": _days(n), "synthetic": False,
        "searches": 12, "silver_searches": 12, "covered": 9, "uncovered": 2, "failed": 1,
        "avg_latency_ms": 85.0})
    return lake.with_outcome_total(df, ["covered", "uncovered", "failed"])


def gold_gaps() -> pd.DataFrame:
    return pd.DataFrame({
        "tenant_id": PLATFORM_TENANT, "call_date": DAY, "synthetic": False,
        "question": ["what are your opening hours", "do you deliver on sundays"],
        "asked": [3, 1], "best_similarity": [0.31, 0.12]})


def gold_documents() -> pd.DataFrame:
    return pd.DataFrame({
        "tenant_id": PLATFORM_TENANT, "call_date": DAY, "synthetic": False,
        "document_title": ["Refund policy", "Opening hours"], "uses": [4, 1]})


def gold_hours() -> pd.DataFrame:
    df = pd.DataFrame({"tenant_id": "acme-delivery", "call_date": DAY, "synthetic": True,
                       "hour": [9, 10, 14], "calls": [3, 5, 2]})
    df = lake.with_day_calls(df)
    df["silver_calls"] = 10
    return df


def review(n: int = 20) -> pd.DataFrame:
    transcript = np.array([{"at": datetime(2026, 11, 25, 9, tzinfo=UTC), "rank": 1, "seq": 1,
                            "who": "agent", "text": "Hello, how can I help?"},
                           {"at": datetime(2026, 11, 25, 9, 1, tzinfo=UTC), "rank": 0, "seq": 1,
                            "who": "caller", "text": "My parcel [code] is late."}], dtype=object)
    df = pd.DataFrame({
        "tenant_id": "acme-delivery", "call_date": DAY, "synthetic": True,
        "call_id": [f"C{i:04d}" for i in range(n)],
        "started_at": pd.Timestamp("2026-11-25 09:00", tz="UTC"),
        "reason": "escalated,negative_end", "last_emotion": "angry", "duration_s": 61.5,
        "turns": 4, "transcript": [transcript] * n})
    return lake.with_transcript_text(df)


def test_clean_gold_insights_pass(context):
    _clean(context, "gold_sentiment_daily", gold_sentiment())
    _clean(context, "gold_call_sentiment_daily", gold_call_sentiment())
    _clean(context, "gold_tool_daily", gold_tools())
    _clean(context, "gold_knowledge_daily", gold_knowledge())
    _clean(context, "gold_knowledge_gaps", gold_gaps())
    _clean(context, "gold_document_usage", gold_documents())
    _clean(context, "gold_hourly", gold_hours())
    _clean(context, "gold_calls_to_review", review())


def test_gold_sentiment_that_does_not_add_up_to_silver_is_critical(context):
    df = gold_sentiment()
    df.loc[0, "utterances"] = 10
    assert "expect_column_pair_values_to_be_equal:utterances vs silver_utterances" in _verdict(
        context, "gold_sentiment_daily", df)["critical"]


def test_trends_that_do_not_add_up_to_the_calls_are_critical(context):
    df = gold_call_sentiment()
    df.loc[0, "stable"] = 9
    df = lake.with_outcome_total(df, ["improving", "stable", "worsening"])
    assert "expect_column_pair_values_to_be_equal:outcome_total vs calls" in _verdict(
        context, "gold_call_sentiment_daily", df)["critical"]


def test_more_risky_calls_than_calls_is_critical(context):
    df = gold_call_sentiment()
    df.loc[0, "escalation_risk_calls"] = 11
    assert any("escalation_risk_calls" in r for r in _verdict(
        context, "gold_call_sentiment_daily", df)["critical"])


def test_tool_outcomes_must_split_the_calls(context):
    df = gold_tools()
    df.loc[0, "ok"] = 9
    df = lake.with_outcome_total(df, ["ok", "errors", "timeouts"])
    assert "expect_column_pair_values_to_be_equal:outcome_total vs calls" in _verdict(
        context, "gold_tool_daily", df)["critical"]


def test_knowledge_counts_must_reconcile_with_silver(context):
    df = gold_knowledge()
    df.loc[0, "silver_searches"] = 6                   # half the searches lost in the join
    assert "expect_column_pair_values_to_be_equal:searches vs silver_searches" in _verdict(
        context, "gold_knowledge_daily", df)["critical"]


def test_a_duplicate_gap_question_is_critical(context):
    df = pd.concat([gold_gaps(), gold_gaps().iloc[[0]]], ignore_index=True)
    assert any(r.startswith("expect_compound_columns_to_be_unique")
               for r in _verdict(context, "gold_knowledge_gaps", df)["critical"])


def test_an_identifier_in_a_gap_question_is_critical(context):
    df = gold_gaps()
    df.loc[0, "question"] = "is line 0142887711 down"
    assert "expect_column_values_to_not_match_regex:question" in _verdict(
        context, "gold_knowledge_gaps", df)["critical"]


def test_hours_that_do_not_add_up_to_the_day_are_critical(context):
    df = gold_hours()
    df["silver_calls"] = 11
    assert "expect_column_pair_values_to_be_equal:day_calls vs silver_calls" in _verdict(
        context, "gold_hourly", df)["critical"]


def test_an_hour_out_of_range_is_critical(context):
    df = gold_hours()
    df.loc[0, "hour"] = 24
    assert "expect_column_values_to_be_between:hour" in _verdict(
        context, "gold_hourly", df)["critical"]


def test_an_identifier_in_a_review_transcript_is_critical(context):
    df = review()
    lines = list(df.loc[0, "transcript"])
    lines.append({"who": "caller", "text": "my number is 01 42 88 77 11"})
    df.at[0, "transcript"] = np.array(lines, dtype=object)
    df = lake.with_transcript_text(df)
    assert "expect_column_values_to_not_match_regex:transcript_text" in _verdict(
        context, "gold_calls_to_review", df)["critical"]


def test_an_unknown_review_reason_is_critical(context):
    df = review()
    df.loc[0, "reason"] = "bored"
    assert "expect_column_values_to_be_in_set:reason" in _verdict(
        context, "gold_calls_to_review", df)["critical"]


def test_transcript_text_tolerates_missing_transcripts():
    df = pd.DataFrame({"transcript": [None, np.nan, np.array([{"who": "agent", "text": "Hi"}])]})
    assert list(lake.with_transcript_text(df)["transcript_text"]) == ["", "", "Hi"]


def test_a_table_not_built_yet_reads_empty_and_passes(context, monkeypatch):
    from deltalake.exceptions import TableNotFoundError

    def missing(*_args, **_kwargs):
        raise TableNotFoundError("no table")

    monkeypatch.setattr(lake, "read", missing)
    df = lake.silver_knowledge_searches("2026-11-25")
    assert df.empty and set(lake.SILVER_KNOWLEDGE_SEARCHES) <= set(df.columns)
    _clean(context, "silver_knowledge_searches", df)
    gold = lake.gold_knowledge_daily("2026-11-25")
    assert gold.empty and {"outcome_total", "silver_searches"} <= set(gold.columns)
    _clean(context, "gold_knowledge_daily", gold)


def test_reconcile_counts_silver_rows_per_key():
    gold = pd.DataFrame({"tenant_id": ["t", "t"], "call_date": [DAY, DAY],
                         "synthetic": [True, False], "calls": [2, 1]})
    silver = pd.DataFrame({"tenant_id": ["t"] * 3, "call_date": [DAY] * 3,
                           "synthetic": [True, True, None]})
    out = lake._reconcile(gold, silver, lake.KEY, "silver_calls")
    assert list(out["silver_calls"]) == [2, 1]          # NULL synthetic counts as real traffic


# -- known tenants: the YAML test tenants, plus active platform clients -----------------

def test_without_a_platform_url_only_yaml_tenants_are_known():
    assert lake.known_tenants() == lake.yaml_tenants()
    assert "acme-delivery" in lake.known_tenants()
    assert lake.platform_tenants(None) == frozenset() and lake.platform_tenants("") == frozenset()


def test_platform_tenants_are_read_from_the_portal(monkeypatch):
    seen = {}

    class Conn:
        read_only = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            seen["sql"], seen["read_only"] = sql, self.read_only
            return self

        def fetchall(self):
            return [(uuid.UUID(PLATFORM_TENANT),)]

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda url, **kw: seen.setdefault("url", url) and Conn())
    monkeypatch.setenv("PLATFORM_DATABASE_URL", "postgresql://voice_reader:x@db/tenant_portal")
    assert lake.known_tenants() == lake.yaml_tenants() | {PLATFORM_TENANT}
    # voice_reader may read four columns of accounts_tenant; ask for exactly what is needed.
    assert seen["sql"] == "SELECT id FROM accounts_tenant WHERE is_active"
    assert seen["read_only"] is True and seen["url"].endswith("/tenant_portal")


def test_a_platform_tenant_passes_the_unknown_tenant_rule(context, monkeypatch):
    monkeypatch.setattr(lake, "platform_tenants", lambda url: frozenset({PLATFORM_TENANT}))
    monkeypatch.setenv("PLATFORM_DATABASE_URL", "postgresql://x")
    df = searches()
    df["tenant_id"] = PLATFORM_TENANT
    _clean(context, "silver_knowledge_searches", df)


def test_platform_calls_without_the_portal_are_critical(context):
    df = searches()
    df["tenant_id"] = PLATFORM_TENANT                  # no PLATFORM_DATABASE_URL: unknown
    assert "expect_column_values_to_be_in_set:tenant_id" in _verdict(
        context, "silver_knowledge_searches", df)["critical"]
