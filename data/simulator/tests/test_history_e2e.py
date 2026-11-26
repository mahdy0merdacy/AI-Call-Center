"""Backdated calls through the REAL orchestrator servicer, locally, with no network.

The orchestrator code is the real one (OrchestratorServicer.Session, the agent,
the flow engine, the tool registry's allowlist and timing, the event publisher's
framing). Swapped for stand-ins: the Kafka producer (kept in memory), the MCP
transport (answers from tenants/fixtures, as the MCP servers do), the knowledge
service (word overlap over the policy text) and the LLM (fake_llm.py, served on
127.0.0.1). What comes out is decoded the way a consumer would.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

import history as h
import history_scenarios as sc
import pytest
from aicc_events import ev
from app.flow import TENANTS_DIR
from app.server import OrchestratorServicer

PARIS = h.resolve_tz("Europe/Paris")
FX = sc.Fixtures.load(TENANTS_DIR)
STOCKLINE_ID = "6f1c2e4a-0b7d-4c55-9a1e-3d2f8b9c0a11"
SLUGS = {"acme": "acme", "swiftnet": "swiftnet", "stockline": "stockline"}
DAY = date(2026, 11, 4)                 # a Wednesday, three weeks before "today"
NEXT_MONDAY = date(2026, 11, 9)         # "next Monday" as said on DAY


def _plan(i: int, tenant: str, hour: int, lines: list[str]) -> h.CallPlan:
    start = h.local_epoch_ms(DAY, hour * 3600 + 17 * 60, PARIS)
    return h.CallPlan(i, tenant, start, DAY, sc.Script("test", lines), [False] * len(lines), i)


def _run(plans: list[h.CallPlan]):
    async def go():
        undo = h.install_fixture_tools(FX)
        runner, llm_url = await h.start_fake_llm(ttft_ms_median=5)
        try:
            pub, producer = h.memory_publisher()
            directory = await h.build_directory(
                list(SLUGS), SLUGS, tenants_dir=TENANTS_DIR, stockline_id=STOCKLINE_ID,
                stockline_name="Stockline", dsn=None)
            servicer = OrchestratorServicer(directory, "k", "sim-llm", dict(h.OFFLINE_MCP), pub,
                                            llm_url=llm_url, knowledge=h.OfflineKnowledge())
            results = await h.run_calls(servicer, pub, plans, SLUGS, concurrency=3)
        finally:
            await runner.cleanup()
            undo()
        return results, pub, producer
    return asyncio.run(go())


@pytest.fixture(scope="module")
def three_calls():
    plans = [
        _plan(0, "acme", 10, ["where is my parcel", "a c four four seven one x two zero nine",
                              "can you deliver it next Monday please", "great, thanks so much!"]),
        _plan(1, "swiftnet", 11, ["my internet is down", "0142887711",
                                  "the light is solid red"]),
        _plan(2, "stockline", 14, ["How long do we keep stock records?",
                                   "How do I reset my password?"]),
    ]
    results, pub, producer = _run(plans)
    events = defaultdict(list)
    for e in producer.events():
        events[e.call_id].append(e)
    return {r.plan.tenant: (r, events[r.call_id]) for r in results}, pub


def _kinds(evs: list[ev.Event]) -> list[str]:
    return [e.WhichOneof("payload") for e in evs]


def _ulid_ms(ulid: str) -> int:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    n = 0
    for c in ulid[:10]:
        n = n * 32 + alphabet.index(c)
    return n


def test_every_call_completes(three_calls):
    calls, _ = three_calls
    assert set(calls) == {"acme", "swiftnet", "stockline"}
    for result, evs in calls.values():
        assert result.ok, result.error
        assert evs, result.plan.tenant


def test_events_carry_the_backdated_time(three_calls):
    calls, _ = three_calls
    real_now_ms = datetime.now().timestamp() * 1000
    for result, evs in calls.values():
        start = result.plan.start_ms
        stamps = [e.occurred_at_ms for e in evs]
        assert stamps == sorted(stamps)
        assert abs(stamps[0] - start) < 1_000
        assert all(start <= t < start + 30 * 60_000 for t in stamps)
        assert all(t < real_now_ms - 7 * 86_400_000 for t in stamps)
        assert h.local_date(stamps[0], PARIS) == DAY
        # the call is as long as a phone call, not as long as the in-process run
        ended = evs[-1].call_ended
        assert ended.duration_ms == pytest.approx(stamps[-1] - start, abs=50)
        assert ended.duration_ms > 8_000
        # the call id is a ULID of the virtual start
        assert _ulid_ms(result.call_id) == start


def test_calls_are_marked_synthetic_history(three_calls):
    calls, _ = three_calls
    for tenant, (_result, evs) in calls.items():
        assert _kinds(evs)[0] == "call_started" and _kinds(evs)[-1] == "call_ended"
        started = evs[0].call_started
        assert started.synthetic is True
        assert started.channel == "history"
        assert started.tenant_slug == SLUGS[tenant]
        assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
        assert {e.source.service for e in evs} == {ev.SERVICE_ORCHESTRATOR}


def test_acme_runs_its_tools_with_today_pinned_to_the_call_day(three_calls):
    calls, _ = three_calls
    _, evs = calls["acme"]
    assert {e.tenant_id for e in evs} == {"acme-delivery"}
    kinds = _kinds(evs)
    assert kinds.count("utterance_transcribed") == 4
    assert "knowledge_searched" not in kinds
    tools = [(e.tool_invoked.tool, e.tool_invoked.outcome, json.loads(e.tool_invoked.result_json))
             for e in evs if e.WhichOneof("payload") == "tool_invoked"]
    assert [(t, o) for t, o, _ in tools] == [("lookup_shipment", "in_transit"),
                                             ("reschedule_delivery", "ok")]
    # "next Monday" counted from the call's virtual day, not from the real today
    assert tools[1][2]["new_date"] == NEXT_MONDAY.isoformat()
    filled = [e.slot_filled for e in evs if e.WhichOneof("payload") == "slot_filled"]
    assert [(s.slot, s.value) for s in filled][-1] == ("new_date", NEXT_MONDAY.isoformat())


def test_swiftnet_checks_the_line(three_calls):
    calls, _ = three_calls
    _, evs = calls["swiftnet"]
    assert {e.tenant_id for e in evs} == {"swiftnet-isp"}
    tools = [(e.tool_invoked.tool, e.tool_invoked.outcome)
             for e in evs if e.WhichOneof("payload") == "tool_invoked"]
    assert tools == [("check_line_status", "line_ok")]
    assert _kinds(evs).count("utterance_transcribed") == 3


def test_stockline_searches_its_document(three_calls):
    calls, pub = three_calls
    _, evs = calls["stockline"]
    assert {e.tenant_id for e in evs} == {STOCKLINE_ID}
    searches = [e.knowledge_searched for e in evs if e.WhichOneof("payload") == "knowledge_searched"]
    assert [(s.query, s.covered, s.failed) for s in searches] == [
        ("How long do we keep stock records?", True, False),
        ("How do I reset my password?", False, False),
    ]
    assert list(searches[0].document_titles) == ["Stock Control Policy"]
    answers = [e.turn_completed.spoken_text for e in evs
               if e.WhichOneof("payload") == "turn_completed" and e.turn_completed.turn_index > 0]
    assert "7 years" in answers[0]
    assert "don't cover" in answers[1]
    assert pub.tally["stockline"]["search_uncovered"] == 1
    assert pub.gaps["stockline"]["How do I reset my password?"] == 1


def test_a_caller_with_no_usable_id_is_escalated():
    plan = _plan(0, "acme", 9, ["where is my parcel"] + sc.ACME_BAD_IDS[:3])
    (result,), pub, producer = _run([plan])
    evs = producer.events()
    assert result.ok and result.hung_up_by_agent
    kinds = _kinds(evs)
    assert kinds.count("slot_rejected") == 3 and "escalated" in kinds
    assert evs[-1].call_ended.reason == ev.END_REASON_ESCALATED
    assert pub.tally["acme"]["escalated"] == 1
    assert all(e.occurred_at_ms - plan.start_ms < timedelta(minutes=10).total_seconds() * 1000
               for e in evs)
