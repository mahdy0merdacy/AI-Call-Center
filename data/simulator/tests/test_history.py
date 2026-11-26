"""The pure parts of the history generator: traffic shape, scenarios, question
pools, and the virtual clock. Nothing here touches the network.

    .venv/bin/python -m pytest data/simulator/tests
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone

import history as h
import history_scenarios as sc
import pytest
from app.dates import parse_date
from app.flow import TENANTS_DIR, load_tenants, route_by_slot, route_scored

PARIS = h.resolve_tz("Europe/Paris")
NOW = datetime(2026, 11, 26, 15, 0, tzinfo=PARIS)
FX = sc.Fixtures.load(TENANTS_DIR)


@pytest.fixture(scope="module")
def plans() -> list[h.CallPlan]:
    return h.build_plan(h.PlanConfig(days=30, seed=7, tz=PARIS, now=NOW), FX)


@pytest.fixture(scope="module")
def tenants():
    return load_tenants(TENANTS_DIR)


# -- the plan ---------------------------------------------------------------------


def test_the_plan_is_the_same_for_the_same_seed():
    cfg = h.PlanConfig(days=5, seed=99, tz=PARIS, now=NOW)
    a, b = h.build_plan(cfg, FX), h.build_plan(cfg, FX)
    assert [(p.tenant, p.start_ms, p.script.lines) for p in a] == \
           [(p.tenant, p.start_ms, p.script.lines) for p in b]
    other = h.build_plan(h.PlanConfig(days=5, seed=100, tz=PARIS, now=NOW), FX)
    assert [p.start_ms for p in other] != [p.start_ms for p in a]


def test_every_call_is_inside_the_window_and_in_the_past(plans):
    first = datetime(2026, 10, 28, tzinfo=PARIS).timestamp() * 1000
    latest = NOW.timestamp() * 1000 - 120_000
    assert all(first <= p.start_ms <= latest for p in plans)
    assert [p.start_ms for p in plans] == sorted(p.start_ms for p in plans)
    assert {p.day for p in plans} == {date(2026, 10, 28) + timedelta(days=i) for i in range(30)}
    # a call's day is its LOCAL date
    assert all(h.local_date(p.start_ms, PARIS) == p.day for p in plans)


def test_volume_is_30_to_60_calls_a_weekday_per_tenant(plans):
    full_days = {p.day for p in plans} - {NOW.date()}
    for tenant in h.TENANT_KEYS:
        per_day = Counter(p.day for p in plans if p.tenant == tenant)
        weekdays = [per_day[d] for d in full_days if d.weekday() < 5]
        weekends = [per_day[d] for d in full_days if d.weekday() >= 5]
        avg_weekday = sum(weekdays) / len(weekdays)
        assert 28 <= avg_weekday <= 62, (tenant, avg_weekday)
        assert sum(weekends) / len(weekends) < 0.8 * avg_weekday, tenant


def test_weekends_are_quieter_and_the_staff_hotline_nearly_closed(plans):
    per = Counter((p.tenant, p.day.weekday() >= 5) for p in plans)
    weeks = 30 / 7
    acme_weekday = per[("acme", False)] / (weeks * 5)
    acme_weekend = per[("acme", True)] / (weeks * 2)
    assert acme_weekend < 0.5 * acme_weekday
    assert per[("stockline", True)] / (weeks * 2) < 0.2 * per[("stockline", False)] / (weeks * 5)


def test_office_hours_with_a_lunch_dip(plans):
    hours = Counter(datetime.fromtimestamp(p.start_ms / 1000, PARIS).hour
                    for p in plans if p.tenant == "acme" and p.day.weekday() < 5)
    assert sum(hours[x] for x in range(0, 6)) == 0
    morning, lunch = hours[10] + hours[11], hours[12] + hours[13]
    assert lunch < 0.65 * morning
    assert sum(hours[x] for x in range(9, 18)) > 0.8 * sum(hours.values())


def test_swiftnet_has_one_outage_day():
    cfg = h.PlanConfig(days=30, seed=7, tz=PARIS, now=NOW)
    plans = h.build_plan(cfg, FX)
    day = h.outage_day(cfg)
    assert day is not None and day.weekday() < 5
    outages = Counter(p.day for p in plans if p.script.scenario == "area_outage")
    assert outages.most_common(1)[0][0] == day
    assert outages[day] >= 3 * (sum(outages.values()) - outages[day]) / 29


def test_scenario_mix_follows_the_weights(plans):
    acme = Counter(p.script.scenario for p in plans if p.tenant == "acme")
    n = sum(acme.values())
    escalating = acme["bad_ids_escalated"] + acme["human_requested_escalated"]
    assert 0.07 < escalating / n < 0.19
    assert {"track_in_transit", "track_delivered", "track_delayed", "track_delayed_angry",
            "reschedule_ok", "reschedule_too_late", "not_found_then_found",
            "not_found_gives_up", "human_requested_hung_up", "general_question",
            "bare_id"} <= set(acme)
    swift = Counter(p.script.scenario for p in plans if p.tenant == "swiftnet")
    assert {"line_ok_troubleshoot", "area_outage", "account_suspended", "line_not_found",
            "bad_number_escalated", "faq"} <= set(swift)


def test_barge_ins_are_rare(plans):
    flags = [b for p in plans for b in p.barge_in]
    assert 0.01 < sum(flags) / len(flags) < 0.06


# -- question pools -----------------------------------------------------------------


def test_question_pools_have_the_promised_sizes():
    covered = [q for q, _ in sc.STOCKLINE_COVERED]
    uncovered = [q for q, _ in sc.STOCKLINE_UNCOVERED]
    assert len(covered) >= 40 and len(set(covered)) == len(covered)
    assert len(uncovered) >= 25 and len(set(uncovered)) == len(uncovered)
    assert not set(covered) & set(uncovered)


def test_a_quarter_to_a_third_of_questions_are_uncovered_with_a_clear_top():
    rng = random.Random(3)
    asked = [sc.stockline_question(rng) for _ in range(4000)]
    share = sum(not c for _, c in asked) / len(asked)
    assert 0.25 <= share <= 0.35
    gaps = Counter(q for q, c in asked if not c).most_common()
    (top, n1), (_, n2), *_rest = gaps
    assert top == "How do I reset my password?"
    assert n1 > 1.2 * n2 and n1 > 5 * gaps[-1][1]


def test_the_offline_search_tells_covered_from_uncovered():
    kb = h.OfflineKnowledge()

    def best(q: str) -> float:
        return kb.score(q)[0][0]
    covered = [best(q) >= 0.5 for q, _ in sc.STOCKLINE_COVERED]
    uncovered = [best(q) < 0.5 for q, _ in sc.STOCKLINE_UNCOVERED]
    assert sum(covered) / len(covered) >= 0.9
    assert sum(uncovered) / len(uncovered) >= 0.95


# -- what callers say really drives the flows ------------------------------------------


def test_spoken_codes_are_read_back_by_the_real_slot_parsers(tenants):
    sid_slot = tenants["acme"].flows[0].slots[0]
    phone_slot = tenants["swiftnet"].flows[0].slots[0]
    rng = random.Random(1)
    for _ in range(200):
        sid = rng.choice(list(FX.shipments) + [sc.unknown_shipment(rng, FX)])
        assert sid_slot.extract(sc.speak_code(rng, sid)).upper() == sid
        num = rng.choice(list(FX.lines) + [sc.unknown_line(rng, FX)])
        assert phone_slot.extract(sc.speak_code(rng, num)) == num


def test_bad_ids_and_numbers_never_pass_the_slot(tenants):
    sid_slot = tenants["acme"].flows[0].slots[0]
    phone_slot = tenants["swiftnet"].flows[0].slots[0]
    assert not any(sid_slot.extract(x) for x in sc.ACME_BAD_IDS + sc.ACME_HUMAN
                   + sc.ACME_HUMAN_ANGRY)
    assert not any(phone_slot.extract(x) for x in sc.SWIFT_BAD_NUMBER)


def test_openers_route_where_they_should(tenants):
    acme, swift = tenants["acme"], tenants["swiftnet"]
    for line in sc.ACME_TRACK[:5] + sc.ACME_ANGRY_OPEN:
        flow, _ = route_scored(acme, line)
        assert flow is not None and flow.intent == "track_shipment", line
    for line in sc.SWIFT_FAULT + sc.SWIFT_FAULT_ANGRY:
        flow, _ = route_scored(swift, line)
        assert flow is not None and flow.intent == "report_fault", line
    # Questions must stay open turns: answered from knowledge, no flow, no tool.
    for line in sc.ACME_OPEN + sc.ACME_HUMAN + sc.ACME_HAPPY_CLOSE + sc.ACME_NEUTRAL_CLOSE:
        assert route_scored(acme, line)[0] is None and route_by_slot(acme, line) is None, line
    for line in sc.SWIFT_FAQ + sc.SWIFT_HUMAN + [t for ts in sc.SWIFT_TROUBLESHOOT for t in ts]:
        assert route_scored(swift, line)[0] is None and route_by_slot(swift, line) is None, line


def test_every_reschedule_phrasing_is_a_date():
    estimate = date(2026, 9, 30)
    for line in sc.ACME_RESCHEDULE:
        assert parse_date(line, today=date(2026, 11, 3), base=estimate) is not None, line
    assert parse_date("can you postpone it 3 days", date(2026, 11, 3), estimate) == date(2026, 10, 3)
    assert parse_date("the 12th of December would be better", date(2026, 11, 3),
                      estimate) == date(2026, 12, 12)


# -- clock ----------------------------------------------------------------------------


def test_local_wall_clock_maps_to_epoch_across_the_dst_change():
    # Paris left summer time on 2026-10-25: 10:00 is UTC+2 before, UTC+1 after.
    before = h.local_epoch_ms(date(2026, 10, 24), 10 * 3600, PARIS)
    after = h.local_epoch_ms(date(2026, 10, 26), 10 * 3600, PARIS)
    assert before == int(datetime(2026, 10, 24, 8, tzinfo=UTC).timestamp() * 1000)
    assert after == int(datetime(2026, 10, 26, 9, tzinfo=UTC).timestamp() * 1000)
    assert h.local_date(after, PARIS) == date(2026, 10, 26)
    # a fixed offset when there is no tz database
    assert h.local_epoch_ms(date(2026, 11, 3), 0, timezone(timedelta(hours=1))) == \
        int(datetime(2026, 11, 2, 23, tzinfo=UTC).timestamp() * 1000)


def test_the_call_clock_is_start_plus_real_time_plus_talk_time():
    real = [100.0]
    clock = h.CallClock(1_700_000_000_000, monotonic=lambda: real[0])
    assert clock.now_ms() == 1_700_000_000_000
    real[0] += 0.25                         # a quarter second of real processing
    clock.advance(12.0)                     # twelve seconds of talking
    assert clock.now_ms() == 1_700_000_012_250
    assert clock.elapsed_ms() == 12_250
    clock.advance(-5)                       # time never runs backwards
    assert clock.elapsed_ms() == 12_250


def test_talk_time_is_plausible():
    rng = random.Random(0)
    assert 2.0 < h.agent_speech_s(12) < 6.0
    short = h.caller_turn_s("ok thanks", rng)
    code = h.caller_turn_s("a c four four seven one x two zero nine", rng)
    assert 0.6 < short < 4 and code > short
