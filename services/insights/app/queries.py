"""Reads of the analytics schema, aggregated over a caller's data tenants.

Every function takes the tenant ids it may read and filters on them in SQL; no
query reads a row outside that list. Values only ever travel as parameters.

The tables are daily roll-ups (one row per tenant, day and synthetic flag, plus a
dimension such as the emotion or the tool). Over a range, counts are summed and
rates recomputed from the summed counts, never averaged day by day: a quiet day
must not weigh as much as a busy one.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from psycopg import AsyncConnection

Row = dict[str, Any]

# Common filter, appended to every daily query.
_SCOPE = """
    tenant_id = ANY(%(tenants)s)
    AND day BETWEEN %(from)s AND %(to)s
    AND (%(synthetic)s::boolean IS NULL OR synthetic = %(synthetic)s::boolean)
"""

GAPS_LIMIT = 20
DOCUMENTS_LIMIT = 50


@dataclass(frozen=True)
class Scope:
    tenants: list[str]
    start: date
    end: date  # inclusive
    synthetic: bool | None  # None = both, True = simulator only, False = real only

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "Scope":
        """The period of equal length just before this one."""
        return Scope(
            self.tenants,
            self.start - timedelta(days=self.days),
            self.start - timedelta(days=1),
            self.synthetic,
        )

    def params(self, **extra: Any) -> dict[str, Any]:
        return {
            "tenants": self.tenants,
            "from": self.start,
            "to": self.end,
            "synthetic": self.synthetic,
            **extra,
        }

    def each_day(self) -> Iterable[date]:
        return (self.start + timedelta(days=i) for i in range(self.days))


def rate(num: float, den: float) -> float:
    """num/den rounded, 0.0 when there is nothing to divide by (no data is no error)."""
    return round(num / den, 4) if den else 0.0


async def _all(conn: AsyncConnection[Any], sql: str, params: dict[str, Any]) -> list[tuple]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def _one(conn: AsyncConnection[Any], sql: str, params: dict[str, Any]) -> tuple:
    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    assert row is not None  # aggregates without GROUP BY always return one row
    return row


# ── tenants ──────────────────────────────────────────────────────────────────────


async def linked_tenants(conn: AsyncConnection[Any], portal_tenant: str) -> list[str]:
    rows = await _all(
        conn,
        """
        SELECT data_tenant_id FROM analytics.tenant_links
        WHERE portal_tenant_id = %(portal)s::uuid ORDER BY data_tenant_id
    """,
        {"portal": portal_tenant},
    )
    return [r[0] for r in rows]


async def updated_at(conn: AsyncConnection[Any]) -> str | None:
    (published,) = await _one(conn, "SELECT max(published_at) FROM analytics.publish_runs", {})
    return published.isoformat() if published else None


# ── summary ──────────────────────────────────────────────────────────────────────


async def kpis(conn: AsyncConnection[Any], scope: Scope) -> Row:
    (calls, ended, escalated, duration_sum, duration_weight, p50_sum, p50_weight) = await _one(
        conn,
        f"""
        SELECT coalesce(sum(calls), 0)::bigint,
               coalesce(sum(ended_calls), 0)::bigint,
               coalesce(sum(escalated_calls), 0)::bigint,
               -- Daily means weighted back by the calls behind them.
               coalesce(sum(avg_duration_s::float8 * ended_calls)
                        FILTER (WHERE avg_duration_s IS NOT NULL), 0),
               coalesce(sum(ended_calls) FILTER (WHERE avg_duration_s IS NOT NULL), 0)::bigint,
               coalesce(sum(turn_p50_ms::float8 * calls)
                        FILTER (WHERE turn_p50_ms IS NOT NULL), 0),
               coalesce(sum(calls) FILTER (WHERE turn_p50_ms IS NOT NULL), 0)::bigint
        FROM analytics.tenant_daily WHERE {_SCOPE}
    """,
        scope.params(),
    )
    tool_calls, tool_ok = await _one(
        conn,
        f"""
        SELECT coalesce(sum(calls), 0)::bigint, coalesce(sum(ok), 0)::bigint
        FROM analytics.tool_daily WHERE {_SCOPE}
    """,
        scope.params(),
    )
    return {
        "calls": calls,
        "self_service_rate": rate(ended - escalated, ended),
        "escalation_rate": rate(escalated, ended),
        "avg_duration_s": round(duration_sum / duration_weight, 1) if duration_weight else 0.0,
        "tool_success_rate": rate(tool_ok, tool_calls),
        # Percentiles do not add up: this is the calls-weighted mean of the daily
        # medians, a close stand-in for the median over the range.
        "turn_p50_ms": round(p50_sum / p50_weight) if p50_weight else 0,
        "escalated_calls": escalated,
    }


async def daily_calls(conn: AsyncConnection[Any], scope: Scope) -> list[Row]:
    rows = await _all(
        conn,
        f"""
        SELECT day, sum(calls)::bigint, sum(ended_calls)::bigint, sum(escalated_calls)::bigint
        FROM analytics.tenant_daily WHERE {_SCOPE}
        GROUP BY day
    """,
        scope.params(),
    )
    by_day = {r[0]: r[1:] for r in rows}
    out = []
    for day in scope.each_day():  # every day of the range, so a chart has no holes
        calls, ended, escalated = by_day.get(day, (0, 0, 0))
        out.append(
            {
                "day": day.isoformat(),
                "calls": calls,
                "escalated_calls": escalated,
                "self_service_rate": rate(ended - escalated, ended),
            }
        )
    return out


# ── sentiment ────────────────────────────────────────────────────────────────────


async def sentiment(conn: AsyncConnection[Any], scope: Scope) -> Row:
    rows = await _all(
        conn,
        f"""
        SELECT day, emotion, sum(utterances)::bigint
        FROM analytics.sentiment_daily WHERE {_SCOPE}
        GROUP BY day, emotion
    """,
        scope.params(),
    )
    totals: dict[str, int] = {}
    by_day: dict[date, dict[str, int]] = {}
    for day, emotion, n in rows:
        totals[emotion] = totals.get(emotion, 0) + n
        by_day.setdefault(day, {})[emotion] = n
    total = sum(totals.values())
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    emotions = [{"emotion": e, "utterances": n, "share": rate(n, total)} for e, n in ranked]
    daily = [
        {"day": day.isoformat(), **{e: by_day.get(day, {}).get(e, 0) for e, _ in ranked}}
        for day in scope.each_day()
    ]

    calls = await _one(
        conn,
        f"""
        SELECT coalesce(sum(calls), 0)::bigint,
               coalesce(sum(escalation_risk_calls), 0)::bigint,
               coalesce(sum(improving), 0)::bigint,
               coalesce(sum(stable), 0)::bigint,
               coalesce(sum(worsening), 0)::bigint,
               coalesce(sum(ended_negative), 0)::bigint
        FROM analytics.call_sentiment_daily WHERE {_SCOPE}
    """,
        scope.params(),
    )
    keys = ("total", "escalation_risk", "improving", "stable", "worsening", "ended_negative")
    return {"emotions": emotions, "daily": daily, "calls": dict(zip(keys, calls, strict=True))}


# ── tools ────────────────────────────────────────────────────────────────────────


async def tools(conn: AsyncConnection[Any], scope: Scope) -> Row:
    rows = await _all(
        conn,
        f"""
        SELECT tool, sum(calls)::bigint, sum(ok)::bigint, sum(errors)::bigint,
               sum(timeouts)::bigint,
               sum(avg_latency_ms::float8 * calls) FILTER (WHERE avg_latency_ms IS NOT NULL),
               sum(calls) FILTER (WHERE avg_latency_ms IS NOT NULL)
        FROM analytics.tool_daily WHERE {_SCOPE}
        GROUP BY tool
        ORDER BY 2 DESC, tool
    """,
        scope.params(),
    )
    return {
        "tools": [
            {
                "tool": tool,
                "calls": calls,
                "ok": ok,
                "errors": errors,
                "timeouts": timeouts,
                "success_rate": rate(ok, calls),
                "avg_latency_ms": round(lat_sum / lat_weight, 1) if lat_weight else 0.0,
            }
            for tool, calls, ok, errors, timeouts, lat_sum, lat_weight in rows
        ]
    }


# ── knowledge ────────────────────────────────────────────────────────────────────


async def knowledge(conn: AsyncConnection[Any], scope: Scope) -> Row:
    searches, covered, uncovered, failed = await _one(
        conn,
        f"""
        SELECT coalesce(sum(searches), 0)::bigint, coalesce(sum(covered), 0)::bigint,
               coalesce(sum(uncovered), 0)::bigint, coalesce(sum(failed), 0)::bigint
        FROM analytics.knowledge_daily WHERE {_SCOPE}
    """,
        scope.params(),
    )
    documents = await _all(
        conn,
        f"""
        SELECT document_title, sum(uses)::bigint
        FROM analytics.document_usage WHERE {_SCOPE}
        GROUP BY document_title
        ORDER BY 2 DESC, document_title
        LIMIT %(limit)s
    """,
        scope.params(limit=DOCUMENTS_LIMIT),
    )
    gaps = await _all(
        conn,
        f"""
        SELECT question, sum(asked)::bigint, max(day)
        FROM analytics.knowledge_gaps WHERE {_SCOPE}
        GROUP BY question
        ORDER BY 2 DESC, 3 DESC, question
        LIMIT %(limit)s
    """,
        scope.params(limit=GAPS_LIMIT),
    )
    return {
        "searches": searches,
        "covered": covered,
        "uncovered": uncovered,
        "failed": failed,
        # Share of all searches that found an answer (a failed search found none).
        "coverage_rate": rate(covered, searches),
        "documents": [{"title": t, "uses": n} for t, n in documents],
        "gaps": [{"question": q, "asked": n, "last_day": d.isoformat()} for q, n, d in gaps],
    }


# ── activity ─────────────────────────────────────────────────────────────────────


async def activity(conn: AsyncConnection[Any], scope: Scope) -> Row:
    rows = await _all(
        conn,
        f"""
        SELECT extract(isodow FROM day)::int - 1, hour, sum(calls)::bigint
        FROM analytics.hourly WHERE {_SCOPE}
        GROUP BY 1, 2
    """,
        scope.params(),
    )
    cells = {(wd, hour): n for wd, hour, n in rows}
    # All 7 x 24 cells, so the grid is complete without the client filling it in.
    return {
        "heatmap": [
            {"weekday": wd, "hour": h, "calls": cells.get((wd, h), 0)}
            for wd in range(7)
            for h in range(24)
        ]
    }


# ── calls to review ──────────────────────────────────────────────────────────────


async def calls_to_review(conn: AsyncConnection[Any], scope: Scope, limit: int) -> Row:
    rows = await _all(
        conn,
        """
        SELECT call_id, started_at, reason, last_emotion, duration_s, turns, transcript
        FROM analytics.calls_to_review
        WHERE tenant_id = ANY(%(tenants)s)
          AND started_at >= %(start_ts)s AND started_at < %(end_ts)s
          AND (%(synthetic)s::boolean IS NULL OR synthetic = %(synthetic)s::boolean)
        ORDER BY started_at DESC, call_id
        LIMIT %(limit)s
    """,
        scope.params(
            start_ts=datetime.combine(scope.start, time.min, UTC),
            end_ts=datetime.combine(scope.end + timedelta(days=1), time.min, UTC),
            limit=limit,
        ),
    )
    return {
        "calls": [
            {
                "call_id": call_id,
                "started_at": started.isoformat(),
                "reason": reason,
                "last_emotion": emotion,
                "duration_s": duration,
                "turns": turns,
                "transcript": transcript if isinstance(transcript, list) else [],
            }
            for call_id, started, reason, emotion, duration, turns, transcript in rows
        ]
    }
