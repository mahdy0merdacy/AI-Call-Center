"""The flow engine: what the orchestrator ENFORCES, so it gets the most tests."""

from __future__ import annotations

import pytest

from app.flow import CallFlowState, Slot, normalise_spoken, route

# ── spoken-form normalisation ────────────────────────────────────────────────


@pytest.mark.parametrize("spoken, expected", [
    ("A C four four seven one X two zero nine", "AC4471X209"),
    ("double seven", "77"),
    ("triple four two", "4442"),
    ("oh one", "01"),
    ("0, 1, 4, 2", "0142"),
    ("double", "double"),            # dangling multiplier is kept, not dropped
])
def test_normalise_spoken(spoken, expected):
    assert normalise_spoken(spoken) == expected


# ── slot extraction ──────────────────────────────────────────────────────────

CODE = Slot(name="shipment_id", pattern=r"^[A-Za-z0-9]{10}$", prompt="?",
            reprompt="?", type="alphanumeric")
PHONE = Slot(name="phone", pattern=r"^[0-9]{10}$", prompt="?", reprompt="?",
             type="digits")


@pytest.mark.parametrize("utterance, expected", [
    ("AC4471X209", "AC4471X209"),                              # whole utterance
    ("A C four four seven one X two zero nine", "AC4471X209"),  # spoken out
    ("yes it's AC4471X209 thanks", "AC4471X209"),               # embedded
    # Leading filler used to be glued on: a left-to-right span search matched
    # "it is A C four four seven one" -> "itisAC4471", ten alphanumerics with
    # digits, satisfying both the pattern and the digit guard.
    ("it is A C four four seven one X two zero nine okay", "AC4471X209"),
    ("um so it is A C four four seven one X two zero nine", "AC4471X209"),
    ("so the code is AC4471X209 I think", "AC4471X209"),
])
def test_code_slot_accepts(utterance, expected):
    assert CODE.extract(utterance) == expected


@pytest.mark.parametrize("utterance", [
    "um I do not know",        # ten letters can be sliced out of this; no digit
    "AC4471X20",               # nine characters
    "hello there everyone",
    "",
])
def test_code_slot_rejects(utterance):
    assert CODE.extract(utterance) is None


def test_digit_slot_handles_double():
    # The live failure from flow.py: every "double N" used to be dropped.
    assert PHONE.extract("zero one four two three double seven double one five") \
        == "0142377115"


# ── routing ──────────────────────────────────────────────────────────────────


def test_route_matches_example(tenant):
    assert route(tenant, "where is my parcel please").intent == "track_shipment"


def test_route_none_below_threshold(tenant):
    assert route(tenant, "what is the weather like") is None


def test_route_never_picks_internal_flow(tenant):
    # secret_step shares the utterance exactly, but is internal.
    assert route(tenant, "where is my parcel").intent != "secret_step"


# ── slot-filling state machine ───────────────────────────────────────────────


def _started(tenant):
    state = CallFlowState(tenant)
    flow = next(f for f in tenant.flows if f.intent == "track_shipment")
    return state, state.begin(flow)


def test_begin_asks_first_slot_with_its_endpointing(tenant):
    _, action = _started(tenant)
    assert action.kind == "say"
    assert action.text == "Can I take your shipment ID?"
    assert action.next_silence_ms == 1400


def test_valid_value_makes_flow_ready_for_tool(tenant):
    state, _ = _started(tenant)
    action = state.advance("AC4471X209")
    assert action.kind == "ready_for_tool"
    assert state.filled == {"shipment_id": "AC4471X209"}


def test_invalid_value_is_reprompted(tenant):
    state, _ = _started(tenant)
    action = state.advance("no idea")
    assert action.kind == "say"
    assert action.text == "That doesn't look right."
    assert state.attempts["shipment_id"] == 1


def test_escalates_after_max_attempts(tenant):
    state, _ = _started(tenant)
    actions = [state.advance("no idea") for _ in range(3)]
    assert [a.kind for a in actions] == ["say", "say", "escalate"]
    assert actions[-1].text == "Transferring you now."


# ── config loading ───────────────────────────────────────────────────────────


def test_loader_reads_knowledge_and_slot_overrides(tenant):
    assert "Opening hours" in tenant.knowledge_text
    slot = tenant.flows[0].slots[0]
    assert slot.silence_ms == 1400
    assert slot.reprompt == "I need the full ten-character ID."
    assert tenant.language == "en"
