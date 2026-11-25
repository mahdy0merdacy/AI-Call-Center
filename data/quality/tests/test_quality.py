"""The rules catch what they are for, and pass clean data.

    .venv-quality/bin/python -m pytest data/quality/tests -q

No lake needed: frames are built here, shaped like the silver tables, and the
same suites and runner the pipeline uses validate them in an in-memory context.
A rule that silently stops firing fails one of these tests.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import great_expectations as gx
import lake
from run import check

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
N = 200                       # rows; 1 bad row = 0.5%, under the 1% critical tolerance


@pytest.fixture(scope="module")
def context():
    return gx.get_context(mode="ephemeral")


def _verdict(context, table: str, df: pd.DataFrame) -> dict[str, list[str]]:
    """{'critical': [failed rules], 'warning': [failed rules]}."""
    report = check(context, table, df, docs=False)
    return {sev: [f"{f['rule']}:{f['column']}" for f in s["failed"]]
            for sev, s in report["suites"].items()}


def calls(n: int = N) -> pd.DataFrame:
    last = pd.Timestamp(NOW - timedelta(minutes=5))
    df = pd.DataFrame({
        "call_id": [f"C{i:04d}" for i in range(n)],
        "tenant_id": "acme-delivery", "channel": "simulator", "synthetic": True,
        "starts": 1, "ends": 1, "min_seq": 1, "seq_gaps": 0,
        "end_reason": "caller_hung_up", "duration_ms": 30_000,
        "min_ingest_lag_ms": 5, "last_event_at": last,
    })
    return lake.with_open_after_1h(df, NOW)


def turns(n: int = N) -> pd.DataFrame:
    return pd.DataFrame({
        "call_id": [f"C{i // 4:04d}" for i in range(n)], "turn_index": [i % 4 for i in range(n)],
        "kind": "say", "route_score": 0.5, "llm_ttft_ms": 300, "tool_latency_ms": 90,
        "turn_total_ms": 900,
        "spoken_text": "Your parcel [code] will arrive on 2026-09-30.",
    })


# -- clean data passes ---------------------------------------------------------------

def test_clean_calls_pass_everything(context):
    assert _verdict(context, "silver_calls", calls()) == {"critical": [], "warning": []}


def test_clean_turns_pass_everything(context):
    assert _verdict(context, "silver_turns", turns()) == {"critical": [], "warning": []}


# -- each fault from data/simulator/faults.py is caught, at the right severity ----------

def test_a_duplicate_call_row_is_critical(context):
    df = pd.concat([calls(), calls().iloc[[0]]], ignore_index=True)
    assert "expect_column_values_to_be_unique:call_id" in _verdict(context, "silver_calls", df)["critical"]


def test_one_seq_gap_warns_but_many_are_critical(context):
    one = calls()
    one.loc[0, "seq_gaps"] = 1
    v = _verdict(context, "silver_calls", one)
    assert v["critical"] == [] and "expect_column_values_to_be_in_set:seq_gaps" in v["warning"]

    many = calls()
    many.loc[:9, "seq_gaps"] = 1                       # 5% of calls lost an event
    assert "expect_column_values_to_be_in_set:seq_gaps" in _verdict(context, "silver_calls", many)["critical"]


def test_a_call_that_never_ended_warns(context):
    df = calls()
    df.loc[0, ["ends", "last_event_at"]] = [0, pd.Timestamp(NOW - timedelta(hours=2))]
    df = lake.with_open_after_1h(df, NOW)
    v = _verdict(context, "silver_calls", df)
    assert v["critical"] == [] and "expect_column_values_to_be_in_set:open_after_1h" in v["warning"]


def test_a_call_still_in_progress_is_not_flagged(context):
    df = calls()
    df.loc[0, "ends"] = 0                              # last event 5 minutes ago
    assert _verdict(context, "silver_calls", lake.with_open_after_1h(df, NOW))["warning"] == []


def test_an_unknown_tenant_warns(context):
    df = calls()
    df.loc[0, "tenant_id"] = "ghost-co"
    v = _verdict(context, "silver_calls", df)
    assert v["critical"] == [] and "expect_column_values_to_be_in_set:tenant_id" in v["warning"]


def test_a_clock_running_ahead_warns(context):
    df = calls()
    df.loc[0, "min_ingest_lag_ms"] = -15 * 60_000      # stamped 15 minutes in the future
    assert "expect_column_values_to_be_between:min_ingest_lag_ms" in _verdict(
        context, "silver_calls", df)["warning"]


def test_an_llm_latency_outlier_warns(context):
    df = turns()
    df.loc[0, "llm_ttft_ms"] = 95_000
    v = _verdict(context, "silver_turns", df)
    assert v["critical"] == [] and "expect_column_values_to_be_between:llm_ttft_ms" in v["warning"]


def test_a_duplicate_turn_is_critical(context):
    df = pd.concat([turns(), turns().iloc[[0]]], ignore_index=True)
    assert any(r.startswith("expect_compound_columns_to_be_unique")
               for r in _verdict(context, "silver_turns", df)["critical"])


@pytest.mark.parametrize("leak", [
    "I've noted 01 42 88 77 11 for you.",
    "We'll email jane.doe@example.com.",
    "Parcel AC4471X209 is on its way.",
])
def test_an_identifier_in_spoken_text_is_critical(context, leak):
    df = turns()
    df.loc[0, "spoken_text"] = leak
    assert "expect_column_values_to_not_match_regex:spoken_text" in _verdict(
        context, "silver_turns", df)["critical"]


# -- the identifier pattern itself --------------------------------------------------------

@pytest.mark.parametrize("text", ["0142887711", "01 42 88 77 11", "01.42.88.77.11",
                                  "jane.doe@example.com", "AC4471X209", "ac4471x209"])
def test_identifier_pattern_finds(text):
    assert lake.IDENTIFIER_PATTERN.search(text)


@pytest.mark.parametrize("text", ["arrive on 2026-09-30", "[code] and [number] and [email]",
                                  "call us 9 to 6", "turn 12 of 40", "Lyon sorting centre"])
def test_identifier_pattern_ignores(text):
    assert not lake.IDENTIFIER_PATTERN.search(text)


def test_free_text_columns_come_from_the_pii_options():
    cols = lake.free_text_columns()
    assert "payload.turn_completed.spoken_text" in cols
    assert "payload.slot_rejected.utterance" in cols
    assert "payload.call_started.tenant_slug" not in cols


def gold(n: int = 4) -> pd.DataFrame:
    return pd.DataFrame({
        "tenant_id": "acme-delivery", "synthetic": True,
        "call_date": pd.date_range("2026-11-20", periods=n).date,
        "calls": 50, "silver_calls": 50,
        "escalation_rate": 0.1, "self_service_rate": 0.9, "tool_success_rate": 1.0,
        "slot_rejection_rate": 0.05, "barge_in_rate": 0.02,
        "llm_ttft_p50_ms": 300, "llm_ttft_p95_ms": 560,
    })


def test_clean_gold_passes(context):
    assert _verdict(context, "gold_tenant_daily", gold()) == {"critical": [], "warning": []}


def test_gold_counts_that_do_not_add_up_to_silver_are_critical(context):
    df = gold()
    df.loc[0, "calls"] = 100                           # a join that doubled the rows
    assert "expect_column_pair_values_to_be_equal:calls vs silver_calls" in _verdict(
        context, "gold_tenant_daily", df)["critical"]


def test_a_rate_above_one_is_critical(context):
    df = gold()
    df.loc[0, "self_service_rate"] = 1.4
    assert "expect_column_values_to_be_between:self_service_rate" in _verdict(
        context, "gold_tenant_daily", df)["critical"]
