"""The data-quality rules, as code: one function per checked table.

Each returns {"critical": [...], "warning": [...]}, lists of GX expectations.

  critical  the data is WRONG or UNSAFE: duplicates after the MERGE, an unknown
            tenant, an identifier in free text. Fails the run, so the next layer
            is not built from it.
  warning   the data is SUSPICIOUS or INCOMPLETE: one lost event, a call that
            never ended, a clock running fast. Reported, not blocking: holding
            back a whole day's numbers for one crashed call would be worse.

Several rules appear in both lists at different tolerances (GX's `mostly`):
any lost event is a warning, losses on more than 1% of calls are critical.

Why GX at all, when the Schema Registry already checks every record: the
registry checks SHAPE (is this a valid Event?). These check TRUTH (is this
event a duplicate, from a real tenant, in order, on time?). A record can pass
the first and still be wrong. See DDIA.md for the idea behind each rule.
"""

from __future__ import annotations

import great_expectations.expectations as gxe
from lake import IDENTIFIER_PATTERN, enum_names, event_types, known_tenants

MINUTE_MS = 60_000


def bronze() -> dict[str, list]:
    """The raw batch, before silver is built from it (the gate)."""
    return {
        "critical": [
            *(gxe.ExpectColumnValuesToNotBeNull(column=c) for c in
              ("event_id", "call_id", "tenant_id", "occurred_at", "event_type")),
            gxe.ExpectColumnValuesToBeInSet(column="event_type", value_set=sorted(event_types())),
            gxe.ExpectColumnValuesToBeBetween(column="seq", min_value=1),
        ],
        "warning": [
            # Duplicates are EXPECTED in bronze (producer retries); silver removes
            # them. Many of them means a producer is misconfigured.
            gxe.ExpectColumnProportionOfUniqueValuesToBeBetween(column="event_id", min_value=0.99),
            # Producer clock vs broker clock (DDIA: unreliable clocks). Positive
            # lag is delay; NEGATIVE lag means the producer's clock is ahead.
            gxe.ExpectColumnValuesToBeBetween(column="ingest_lag_ms", min_value=-MINUTE_MS,
                                              max_value=5 * MINUTE_MS, mostly=0.999),
        ],
    }


def bronze_batch_stats() -> dict[str, list]:
    """Table-level facts of the batch, as a one-row frame."""
    return {
        "critical": [
            # A burst of undecodable records: a producer on the wrong schema.
            gxe.ExpectColumnValuesToBeBetween(column="quarantine_rate", max_value=0.05),
            gxe.ExpectColumnValuesToBeBetween(column="events", min_value=1),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeBetween(column="quarantine_rate", max_value=0.001),
        ],
    }


def silver_call_events() -> dict[str, list]:
    return {
        "critical": [
            # The MERGE's one promise: each event exactly once (effectively-once).
            gxe.ExpectColumnValuesToBeUnique(column="event_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="event_date"),
            gxe.ExpectColumnValuesToBeInSet(column="tenant_id", value_set=sorted(known_tenants()),
                                            mostly=0.99),
            # Masking must have removed every identifier from free text.
            gxe.ExpectColumnValuesToNotMatchRegex(column="free_text",
                                                  regex=IDENTIFIER_PATTERN.pattern),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeInSet(column="tenant_id", value_set=sorted(known_tenants())),
        ],
    }


def silver_calls() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectColumnValuesToBeUnique(column="call_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="synthetic", mostly=0.99),
            gxe.ExpectColumnValuesToBeInSet(column="tenant_id", value_set=sorted(known_tenants()),
                                            mostly=0.99),
            gxe.ExpectColumnValuesToBeBetween(column="starts", max_value=1),
            gxe.ExpectColumnValuesToBeBetween(column="ends", max_value=1),
            # Ordering within a partition: seq 1..N with nothing missing.
            gxe.ExpectColumnValuesToBeInSet(column="seq_gaps", value_set=[0], mostly=0.99),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeInSet(column="tenant_id", value_set=sorted(known_tenants())),
            gxe.ExpectColumnValuesToBeInSet(column="seq_gaps", value_set=[0]),
            gxe.ExpectColumnValuesToBeInSet(column="min_seq", value_set=[1]),
            # A call still open an hour after its last event: a crashed process.
            gxe.ExpectColumnValuesToBeInSet(column="open_after_1h", value_set=[False]),
            gxe.ExpectColumnValuesToBeInSet(column="channel",
                                            value_set=["webrtc", "simulator", "fault", "smoke"]),
            gxe.ExpectColumnValuesToBeInSet(column="end_reason",
                                            value_set=sorted(enum_names("EndReason", "END_REASON_"))),
            gxe.ExpectColumnValuesToBeBetween(column="duration_ms", min_value=0,
                                              max_value=60 * MINUTE_MS),
            # Events stamped more than a minute ahead of the broker's clock.
            gxe.ExpectColumnValuesToBeBetween(column="min_ingest_lag_ms", min_value=-MINUTE_MS),
        ],
    }


def silver_turns() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=["call_id", "turn_index"]),
            gxe.ExpectColumnValuesToNotBeNull(column="turn_index"),
            gxe.ExpectColumnValuesToBeInSet(column="kind",
                                            value_set=sorted(enum_names("TurnKind", "TURN_KIND_"))),
            gxe.ExpectColumnValuesToBeBetween(column="route_score", min_value=0, max_value=1),
            gxe.ExpectColumnValuesToNotMatchRegex(column="spoken_text",
                                                  regex=IDENTIFIER_PATTERN.pattern),
        ],
        "warning": [
            # Plausible ranges: values outside them are broken instrumentation
            # more often than slow systems.
            gxe.ExpectColumnValuesToBeBetween(column="llm_ttft_ms", min_value=0, max_value=10_000),
            gxe.ExpectColumnValuesToBeBetween(column="tool_latency_ms", min_value=0, max_value=5_000),
            gxe.ExpectColumnValuesToBeBetween(column="turn_total_ms", min_value=0, max_value=30_000),
        ],
    }


RATES = ["escalation_rate", "self_service_rate", "tool_success_rate",
         "slot_rejection_rate", "barge_in_rate"]


def gold_tenant_daily() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=["tenant_id", "call_date", "synthetic"]),
            gxe.ExpectColumnValuesToBeBetween(column="calls", min_value=1),
            *(gxe.ExpectColumnValuesToBeBetween(column=c, min_value=0, max_value=1) for c in RATES),
            # Reconciliation: every gold count adds up to the silver rows behind it.
            gxe.ExpectColumnPairValuesToBeEqual(column_A="calls", column_B="silver_calls"),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeInSet(column="tenant_id", value_set=sorted(known_tenants())),
            gxe.ExpectColumnPairValuesAToBeGreaterThanB(
                column_A="llm_ttft_p95_ms", column_B="llm_ttft_p50_ms", or_equal=True,
                ignore_row_if="either_value_is_missing"),
        ],
    }


# -- insights ------------------------------------------------------------------------
#
# The sentiment service's classes (services/sentiment/app/emotions.py). The
# contract makes emotion a string so a new class is not a wire change; an
# unknown one is therefore a WARNING (a new model shipped), never critical.
EMOTIONS = ["neutral", "satisfied", "confused", "frustrated", "angry", "anxious", "sad"]
TRENDS = ["improving", "stable", "worsening"]
GOLD_KEY = ["tenant_id", "call_date", "synthetic"]


def _tenant_rules() -> tuple[list, list]:
    """(critical, warning): a few unknown tenants are suspicious, many are wrong."""
    tenants = sorted(known_tenants())
    return ([gxe.ExpectColumnValuesToBeInSet(column="tenant_id", value_set=tenants, mostly=0.99)],
            [gxe.ExpectColumnValuesToBeInSet(column="tenant_id", value_set=tenants)])


def silver_utterance_emotions() -> dict[str, list]:
    critical, warning = _tenant_rules()
    return {
        "critical": [
            gxe.ExpectColumnValuesToBeUnique(column="event_id"),
            # Dated by the call: a row without one was not joined to a call.
            *(gxe.ExpectColumnValuesToNotBeNull(column=c)
              for c in ("call_id", "tenant_id", "call_date", "emotion")),
            gxe.ExpectColumnValuesToBeBetween(column="score", min_value=0, max_value=1),
            *critical,
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeInSet(column="emotion", value_set=EMOTIONS),
            # The service's own count; it restarts when the service does.
            gxe.ExpectCompoundColumnsToBeUnique(column_list=["call_id", "utterance_index"]),
            gxe.ExpectColumnValuesToNotBeNull(column="synthetic", mostly=0.99),
            *warning,
        ],
    }


def silver_call_sentiment() -> dict[str, list]:
    critical, warning = _tenant_rules()
    return {
        "critical": [
            gxe.ExpectColumnValuesToBeUnique(column="call_id"),
            *(gxe.ExpectColumnValuesToNotBeNull(column=c) for c in ("tenant_id", "call_date")),
            gxe.ExpectColumnValuesToBeInSet(column="trend", value_set=TRENDS),
            *critical,
        ],
        "warning": [
            *(gxe.ExpectColumnValuesToBeInSet(column=c, value_set=EMOTIONS)
              for c in ("dominant", "first_emotion", "last_emotion")),
            gxe.ExpectColumnValuesToBeBetween(column="utterances", min_value=1),
            *warning,
        ],
    }


def silver_knowledge_searches() -> dict[str, list]:
    critical, warning = _tenant_rules()
    return {
        "critical": [
            gxe.ExpectColumnValuesToBeUnique(column="event_id"),
            *(gxe.ExpectColumnValuesToNotBeNull(column=c) for c in ("tenant_id", "call_date")),
            # The caller's words, shown to the client as "knowledge gaps": masked.
            gxe.ExpectColumnValuesToNotMatchRegex(column="query",
                                                  regex=IDENTIFIER_PATTERN.pattern),
            # Cosine similarity cannot leave [-1, 1]; outside it is a broken field.
            gxe.ExpectColumnValuesToBeBetween(column="best_similarity", min_value=-1, max_value=1),
            *critical,
        ],
        "warning": [
            # The contract says 0..1 (normalised embeddings).
            gxe.ExpectColumnValuesToBeBetween(column="best_similarity", min_value=0, max_value=1),
            gxe.ExpectColumnValuesToBeBetween(column="latency_ms", min_value=0, max_value=10_000),
            *warning,
        ],
    }


def gold_sentiment_daily() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=[*GOLD_KEY, "emotion"]),
            gxe.ExpectColumnValuesToBeBetween(column="utterances", min_value=1),
            gxe.ExpectColumnPairValuesToBeEqual(column_A="utterances",
                                                column_B="silver_utterances"),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeInSet(column="emotion", value_set=EMOTIONS),
        ],
    }


def gold_call_sentiment_daily() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=GOLD_KEY),
            gxe.ExpectColumnValuesToBeBetween(column="calls", min_value=1),
            gxe.ExpectColumnPairValuesToBeEqual(column_A="calls", column_B="silver_calls"),
            # Every call has exactly one trend.
            gxe.ExpectColumnPairValuesToBeEqual(column_A="outcome_total", column_B="calls"),
            *(gxe.ExpectColumnPairValuesAToBeGreaterThanB(column_A="calls", column_B=c,
                                                          or_equal=True)
              for c in ("escalation_risk_calls", "ended_negative")),
        ],
        "warning": [],
    }


def gold_tool_daily() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=[*GOLD_KEY, "tool"]),
            gxe.ExpectColumnValuesToBeBetween(column="calls", min_value=1),
            gxe.ExpectColumnPairValuesToBeEqual(column_A="calls", column_B="silver_tool_calls"),
            gxe.ExpectColumnPairValuesToBeEqual(column_A="outcome_total", column_B="calls"),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeBetween(column="avg_latency_ms", min_value=0,
                                              max_value=5_000),
        ],
    }


def gold_knowledge_daily() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=GOLD_KEY),
            gxe.ExpectColumnValuesToBeBetween(column="searches", min_value=1),
            gxe.ExpectColumnPairValuesToBeEqual(column_A="searches", column_B="silver_searches"),
            gxe.ExpectColumnPairValuesToBeEqual(column_A="outcome_total", column_B="searches"),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeBetween(column="avg_latency_ms", min_value=0,
                                              max_value=10_000),
        ],
    }


def gold_document_usage() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=[*GOLD_KEY, "document_title"]),
            gxe.ExpectColumnValuesToNotBeNull(column="document_title"),
            gxe.ExpectColumnValuesToBeBetween(column="uses", min_value=1),
        ],
        "warning": [],
    }


def gold_knowledge_gaps() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=[*GOLD_KEY, "question"]),
            gxe.ExpectColumnValuesToNotBeNull(column="question"),
            gxe.ExpectColumnValuesToBeBetween(column="asked", min_value=1),
            # Shown verbatim to the client: no identifier may have survived.
            gxe.ExpectColumnValuesToNotMatchRegex(column="question",
                                                  regex=IDENTIFIER_PATTERN.pattern),
            gxe.ExpectColumnValuesToBeBetween(column="best_similarity", min_value=-1, max_value=1),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeBetween(column="best_similarity", min_value=0, max_value=1),
        ],
    }


def gold_hourly() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=[*GOLD_KEY, "hour"]),
            gxe.ExpectColumnValuesToBeBetween(column="hour", min_value=0, max_value=23),
            gxe.ExpectColumnValuesToBeBetween(column="calls", min_value=1),
            # The hours of a day add up to that day's calls.
            gxe.ExpectColumnPairValuesToBeEqual(column_A="day_calls", column_B="silver_calls"),
        ],
        "warning": [],
    }


def gold_calls_to_review() -> dict[str, list]:
    return {
        "critical": [
            gxe.ExpectColumnValuesToBeUnique(column="call_id"),
            *(gxe.ExpectColumnValuesToNotBeNull(column=c)
              for c in ("tenant_id", "call_date", "started_at", "reason")),
            gxe.ExpectColumnValuesToBeInSet(
                column="reason",
                value_set=["escalated", "negative_end", "escalated,negative_end"]),
            # Transcripts are shown to the client: masked, every line.
            gxe.ExpectColumnValuesToNotMatchRegex(column="transcript_text",
                                                  regex=IDENTIFIER_PATTERN.pattern),
        ],
        "warning": [
            gxe.ExpectColumnValuesToBeInSet(column="last_emotion", value_set=EMOTIONS),
        ],
    }


SUITES = {
    "bronze": bronze,
    "bronze_batch_stats": bronze_batch_stats,
    "silver_call_events": silver_call_events,
    "silver_calls": silver_calls,
    "silver_turns": silver_turns,
    "silver_utterance_emotions": silver_utterance_emotions,
    "silver_call_sentiment": silver_call_sentiment,
    "silver_knowledge_searches": silver_knowledge_searches,
    "gold_tenant_daily": gold_tenant_daily,
    "gold_sentiment_daily": gold_sentiment_daily,
    "gold_call_sentiment_daily": gold_call_sentiment_daily,
    "gold_tool_daily": gold_tool_daily,
    "gold_knowledge_daily": gold_knowledge_daily,
    "gold_document_usage": gold_document_usage,
    "gold_knowledge_gaps": gold_knowledge_gaps,
    "gold_hourly": gold_hourly,
    "gold_calls_to_review": gold_calls_to_review,
}
