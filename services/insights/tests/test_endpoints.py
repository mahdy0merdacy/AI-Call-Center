"""The numbers each endpoint returns, from the fixture rows in conftest.py.

Tenant A (portal PORTAL_A) reads two data tenants: its own id and acme-delivery.
"""

import pytest

from tests.conftest import D0, D1, D2, PORTAL_A, PORTAL_C, TODAY


@pytest.fixture()
def get(client, auth):
    def call(path, tenant=PORTAL_A, **params):
        r = client.get(f"/api/insights/{path}", params=params, headers=auth(tenant=tenant))
        assert r.status_code == 200, r.text
        return r.json()

    return call


# ── summary ──────────────────────────────────────────────────────────────────────


def test_summary_kpis_are_weighted_over_the_range(get):
    body = get("summary")
    assert body["range"] == {"from": "2026-06-01", "to": "2026-06-30", "days": 30}
    # 16 calls, 15 ended, 3 escalated over A's two data tenants.
    assert body["kpis"] == {
        "calls": 16,
        "self_service_rate": 0.8,  # (15 - 3) / 15
        "escalation_rate": 0.2,  # 3 / 15
        "avg_duration_s": 74.0,  # (60*10 + 120*4 + 30*1) / 15
        "tool_success_rate": round(12 / 14, 4),  # ok / calls in tool_daily
        "turn_p50_ms": 850,  # (800*10 + 1000*5 + 600*1) / 16
        "escalated_calls": 3,
    }
    assert body["updated_at"] == "2026-06-30T03:00:00+00:00"


def test_summary_previous_period(get):
    prev = get("summary")["previous"]
    assert prev["calls"] == 4
    assert prev["self_service_rate"] == 0.0
    assert prev["escalation_rate"] == 1.0
    assert prev["avg_duration_s"] == 30.0
    assert prev["turn_p50_ms"] == 500
    assert prev["tool_success_rate"] == 0.0


def test_summary_one_day_compares_with_yesterday(get):
    body = get("summary", days=1)
    assert body["range"] == {"from": "2026-06-30", "to": "2026-06-30", "days": 1}
    assert body["kpis"]["calls"] == 11
    assert body["previous"]["calls"] == 5
    assert [d["day"] for d in body["daily"]] == ["2026-06-30"]


def test_summary_daily_has_every_day(get):
    daily = get("summary")["daily"]
    assert len(daily) == 30
    assert daily[0]["day"] == "2026-06-01"
    by_day = {d["day"]: d for d in daily}
    assert by_day[D0.isoformat()] == {
        "day": D0.isoformat(),
        "calls": 11,
        "escalated_calls": 2,
        "self_service_rate": round(9 / 11, 4),
    }
    assert by_day[D1.isoformat()] == {
        "day": D1.isoformat(),
        "calls": 5,
        "escalated_calls": 1,
        "self_service_rate": 0.75,
    }
    assert by_day["2026-06-15"] == {
        "day": "2026-06-15",
        "calls": 0,
        "escalated_calls": 0,
        "self_service_rate": 0.0,
    }


@pytest.mark.parametrize(
    ("synthetic", "calls", "self_service", "duration", "tools"),
    [
        ("include", 16, 0.8, 74.0, round(12 / 14, 4)),
        ("exclude", 11, round(9 / 11, 4), round(630 / 11, 1), round(10 / 12, 4)),
        ("only", 5, 0.75, 120.0, 1.0),
    ],
)
def test_summary_synthetic_filter(get, synthetic, calls, self_service, duration, tools):
    kpis = get("summary", synthetic=synthetic)["kpis"]
    assert kpis["calls"] == calls
    assert kpis["self_service_rate"] == self_service
    assert kpis["avg_duration_s"] == duration
    assert kpis["tool_success_rate"] == tools


@pytest.mark.parametrize(
    "params", [{"days": 0}, {"days": 366}, {"days": "x"}, {"synthetic": "maybe"}]
)
def test_bad_query_is_422(client, auth, params):
    r = client.get("/api/insights/summary", params=params, headers=auth())
    assert r.status_code == 422


def test_long_range_reaches_old_rows(get):
    assert get("summary", days=365)["kpis"]["calls"] == 16 + 4 + 100


# ── sentiment ────────────────────────────────────────────────────────────────────


def test_sentiment(get):
    body = get("sentiment")
    assert body["emotions"] == [
        {"emotion": "neutral", "utterances": 13, "share": 0.65},
        {"emotion": "satisfied", "utterances": 5, "share": 0.25},
        {"emotion": "angry", "utterances": 2, "share": 0.1},
    ]
    by_day = {d["day"]: d for d in body["daily"]}
    assert len(body["daily"]) == 30
    assert by_day[D0.isoformat()] == {
        "day": D0.isoformat(),
        "neutral": 10,
        "satisfied": 0,
        "angry": 2,
    }
    assert by_day[D1.isoformat()] == {
        "day": D1.isoformat(),
        "neutral": 3,
        "satisfied": 5,
        "angry": 0,
    }
    assert body["calls"] == {
        "total": 16,
        "escalation_risk": 3,
        "improving": 4,
        "stable": 9,
        "worsening": 3,
        "ended_negative": 1,
    }


def test_sentiment_real_calls_only(get):
    body = get("sentiment", synthetic="exclude")
    assert [e["emotion"] for e in body["emotions"]] == ["neutral", "angry"]
    assert body["calls"]["total"] == 11


# ── tools ────────────────────────────────────────────────────────────────────────


def test_tools(get):
    assert get("tools")["tools"] == [
        {
            "tool": "lookup_shipment",
            "calls": 10,
            "ok": 8,
            "errors": 1,
            "timeouts": 1,
            "success_rate": 0.8,
            "avg_latency_ms": 120.0,
        },
        {
            "tool": "create_ticket",
            "calls": 4,
            "ok": 4,
            "errors": 0,
            "timeouts": 0,
            "success_rate": 1.0,
            "avg_latency_ms": 50.0,
        },
    ]


# ── knowledge ────────────────────────────────────────────────────────────────────


def test_knowledge(get):
    body = get("knowledge")
    assert {k: body[k] for k in ("searches", "covered", "uncovered", "failed")} == {
        "searches": 15,
        "covered": 12,
        "uncovered": 2,
        "failed": 1,
    }
    assert body["coverage_rate"] == 0.8
    assert body["documents"] == [
        {"title": "Delivery policy", "uses": 7},
        {"title": "Returns", "uses": 3},
    ]
    assert body["gaps"] == [
        {"question": "Do you ship to Mars?", "asked": 3, "last_day": D0.isoformat()},
        {"question": "Can I pay in gold?", "asked": 1, "last_day": D1.isoformat()},
    ]


def test_knowledge_gaps_follow_the_range(get):
    # Over the last day only, the older "Mars" row on D2 does not count.
    assert D2 < TODAY
    gaps = get("knowledge", days=1)["gaps"]
    assert gaps == [{"question": "Do you ship to Mars?", "asked": 2, "last_day": D0.isoformat()}]


# ── activity ─────────────────────────────────────────────────────────────────────


def test_activity_heatmap(get):
    cells = get("activity")["heatmap"]
    assert len(cells) == 7 * 24
    assert cells[0] == {"weekday": 0, "hour": 0, "calls": 0}
    hot = {(c["weekday"], c["hour"]): c["calls"] for c in cells if c["calls"]}
    assert hot == {(D0.weekday(), 9): 6, (D0.weekday(), 14): 5, (D1.weekday(), 9): 5}
    assert D0.weekday() == 1  # 2026-06-30 is a Tuesday (Mon = 0)


# ── calls to review ──────────────────────────────────────────────────────────────


def test_calls_newest_first(get):
    calls = get("calls")["calls"]
    assert [c["call_id"] for c in calls] == ["a-real", "a-sim"]
    assert calls[0] == {
        "call_id": "a-real",
        "started_at": "2026-06-30T10:00:00+00:00",
        "reason": "escalated",
        "last_emotion": "angry",
        "duration_s": 61.5,
        "turns": 6,
        "transcript": [
            {"who": "caller", "text": "Where is my parcel?"},
            {"who": "agent", "text": "Let me check."},
        ],
    }
    assert calls[1]["last_emotion"] is None


def test_calls_limit_and_filters(get, client, auth):
    assert [c["call_id"] for c in get("calls", limit=1)["calls"]] == ["a-real"]
    assert [c["call_id"] for c in get("calls", synthetic="only")["calls"]] == ["a-sim"]
    assert [c["call_id"] for c in get("calls", days=365)["calls"]] == ["a-real", "a-sim", "a-old"]
    for limit in (0, 51):
        r = client.get("/api/insights/calls", params={"limit": limit}, headers=auth())
        assert r.status_code == 422


# ── empty data ───────────────────────────────────────────────────────────────────

ZERO_KPIS = {
    "calls": 0,
    "self_service_rate": 0.0,
    "escalation_rate": 0.0,
    "avg_duration_s": 0.0,
    "tool_success_rate": 0.0,
    "turn_p50_ms": 0,
    "escalated_calls": 0,
}


@pytest.mark.parametrize("tenant_client", ["client", "empty_client"])
def test_no_data_is_zeros_not_errors(request, auth, tenant_client):
    c = request.getfixturevalue(tenant_client)
    h = auth(tenant=PORTAL_C)

    def get(path):
        r = c.get(f"/api/insights/{path}", headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    summary = get("summary")
    assert summary["kpis"] == ZERO_KPIS
    assert summary["previous"] == ZERO_KPIS
    assert len(summary["daily"]) == 30
    assert all(d["calls"] == 0 for d in summary["daily"])

    sentiment = get("sentiment")
    assert sentiment["emotions"] == []
    assert sentiment["daily"][0] == {"day": "2026-06-01"}
    assert sentiment["calls"] == {
        "total": 0,
        "escalation_risk": 0,
        "improving": 0,
        "stable": 0,
        "worsening": 0,
        "ended_negative": 0,
    }
    assert get("tools") == {"tools": []}
    assert get("knowledge") == {
        "searches": 0,
        "covered": 0,
        "uncovered": 0,
        "failed": 0,
        "coverage_rate": 0.0,
        "documents": [],
        "gaps": [],
    }
    assert all(cell["calls"] == 0 for cell in get("activity")["heatmap"])
    assert get("calls") == {"calls": []}


def test_never_published_has_no_update_time(empty_client, auth):
    body = empty_client.get("/api/insights/summary", headers=auth()).json()
    assert body["updated_at"] is None
