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


SUITES = {
    "bronze": bronze,
    "bronze_batch_stats": bronze_batch_stats,
    "silver_call_events": silver_call_events,
    "silver_calls": silver_calls,
    "silver_turns": silver_turns,
    "gold_tenant_daily": gold_tenant_daily,
}
