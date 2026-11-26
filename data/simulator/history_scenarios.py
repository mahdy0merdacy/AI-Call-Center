"""What the history callers say: scenarios, question pools and traffic shape.

Pure data and pure functions, no I/O beyond reading the tool fixtures, so the
distributions can be unit-tested (data/simulator/tests/test_history.py) and
printed by `history.py --dry-run` without touching anything.

Three tenants:

  acme       parcel tracking and reschedules against the real lookup/reschedule
             tools; callers who get the ID wrong, give up, ask for a person, are
             angry about a delay, or are simply pleased.
  swiftnet   line faults against check_line_status (all four outcomes), and
             questions its in-prompt FAQ answers.
  stockline  a platform tenant: staff calling an internal Stock Control Policy
             hotline. Every utterance is a document search, so the questions are
             split into ones the policy covers and realistic ones it does not
             (payroll, IT, HR...), a few of which come up again and again: those
             are the knowledge gaps worth showing.

Wording carries the emotion (the sentiment worker classifies the caller's text):
angry and frustrated lines, anxious and confused ones, satisfied closers.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# -- tool fixtures --------------------------------------------------------------

# Used when tenants/fixtures is not where we look (the values in the repo today).
_DEFAULT_SHIPMENTS = {
    "AC4471X209": {"outcome": "in_transit", "location": "Lyon sorting centre",
                   "estimated_delivery": "2026-09-30"},
    "AC8823B110": {"outcome": "delivered"},
    "AC1199Z044": {"outcome": "delayed"},
    "AC0000A000": {"outcome": "in_transit", "location": "out for delivery",
                   "reschedulable": False},
}
_DEFAULT_LINES = {
    "0142887711": {"outcome": "line_ok"},
    "0298445102": {"outcome": "area_outage"},
    "0155093388": {"outcome": "account_suspended"},
    "0100000000": {"outcome": "line_ok"},
}


@dataclass(frozen=True)
class Fixtures:
    """The IDs the real tools know, grouped by what looking them up returns."""

    shipments: dict[str, dict[str, Any]]
    lines: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, tenants_dir: Path | None) -> Fixtures:
        def read(name: str, default: dict) -> dict:
            if tenants_dir is not None:
                p = tenants_dir / "fixtures" / name
                if p.exists():
                    data = json.loads(p.read_text())
                    return {k: v for k, v in data.items() if not k.startswith("_")}
            return dict(default)
        return cls(read("shipments.json", _DEFAULT_SHIPMENTS), read("lines.json", _DEFAULT_LINES))

    def shipments_by(self, outcome: str) -> list[str]:
        return sorted(k for k, v in self.shipments.items() if v.get("outcome") == outcome)

    def reschedulable(self) -> list[str]:
        """In transit and still movable: reschedule_delivery answers ok."""
        return sorted(k for k, v in self.shipments.items()
                      if v.get("outcome") == "in_transit" and v.get("reschedulable") is not False
                      and v.get("location") != "out for delivery")

    def too_late(self) -> list[str]:
        """Delivered or out for delivery: reschedule_delivery answers too_late."""
        return sorted(k for k, v in self.shipments.items()
                      if v.get("outcome") == "delivered" or v.get("reschedulable") is False
                      or v.get("location") == "out for delivery")

    def lines_by(self, outcome: str) -> list[str]:
        return sorted(k for k, v in self.lines.items() if v.get("outcome") == outcome)


def unknown_shipment(rng: random.Random, fx: Fixtures) -> str:
    """A well-formed ID the tool has never heard of (outcome not_found)."""
    while True:
        sid = (f"AC{rng.randrange(10_000):04d}{rng.choice('BKMRXZ')}{rng.randrange(1000):03d}")
        if sid not in fx.shipments:
            return sid


def unknown_line(rng: random.Random, fx: Fixtures) -> str:
    while True:
        num = f"0{rng.choice('1234569')}{rng.randrange(10**8):08d}"
        if num not in fx.lines:
            return num


# -- how codes come out of STT ------------------------------------------------------

_DIGIT_WORDS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
                "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}


def speak_code(rng: random.Random, code: str) -> str:
    """One of the ways STT writes a code read aloud; the slot parser takes them all."""
    words = [_DIGIT_WORDS.get(c, c.lower()) for c in code]
    forms = [
        code,
        f"it's {code}",
        " ".join(code),                       # "A C 4 4 7 1 ..."
        ", ".join(code) + ".",                # "A, C, 4, 4, ..."
        " ".join(words),                      # "a c four four seven one ..."
    ]
    if code.isdigit():
        forms.append(" ".join("oh" if c == "0" and rng.random() < 0.5 else w
                              for c, w in zip(code, words, strict=True)))
        forms.append(_doubled(code))
    return rng.choice(forms)


def _doubled(code: str) -> str:
    """'0142887711' -> 'zero one four two double eight double seven double one'."""
    out, i = [], 0
    while i < len(code):
        if i + 1 < len(code) and code[i] == code[i + 1] and (i + 2 >= len(code)
                                                               or code[i + 2] != code[i]):
            out.append(f"double {_DIGIT_WORDS[code[i]]}")
            i += 2
        else:
            out.append(_DIGIT_WORDS[code[i]])
            i += 1
    return " ".join(out)


# -- scripts ---------------------------------------------------------------------


@dataclass
class Script:
    """One caller: what they say, in order, and what we expect it to exercise."""

    scenario: str
    lines: list[str]
    expects: set[str] = field(default_factory=set)   # tool | escalation | search


Builder = Callable[[random.Random, Fixtures], Script]


def _pick(rng: random.Random, options: Sequence[str]) -> str:
    return rng.choice(list(options))


# ---- acme ----

ACME_TRACK = ["where is my parcel", "track my delivery", "has my package shipped",
              "my order hasn't arrived", "I'm calling about a shipment",
              "may locate the parcel.", "hi, I want to track a package",
              "can you tell me where my parcel is"]
ACME_ANGRY_OPEN = [
    "This is ridiculous, my parcel was supposed to be here days ago!",
    "I'm really annoyed, where on earth is my package?",
    "I've been waiting all week for this parcel and nobody tells me anything.",
    "Your tracking is useless, where is my parcel?",
    "I'm absolutely fed up, this is the third time I'm calling about this shipment.",
]
ACME_ANGRY_AFTER_DELAY = [
    "Delayed again? This is completely unacceptable.",
    "That's a joke. I needed it yesterday, I'm so angry right now.",
    "Unbelievable. I'm never ordering with you again.",
    "Are you serious? This is the worst delivery service I've ever used.",
]
ACME_HAPPY_CLOSE = ["great, thanks so much!", "perfect, that's brilliant, thank you",
                    "lovely, thanks for your help", "brilliant, cheers",
                    "oh wonderful, thank you so much"]
ACME_NEUTRAL_CLOSE = ["ok thanks, bye", "alright, bye", "ok", "fine, thanks"]
ACME_RESCHEDULE = [
    "can you postpone it 3 days", "just postpone it for three days",
    "please push it back a week", "can you deliver it on the 12th of December",
    "the 12th of December would be better", "move it to the fifth of October",
    "reschedule it to October 3rd", "I won't be home, can you deliver it on Friday",
    "can you deliver it next Monday please", "postpone it by two days please",
    "I'd rather have it on the 20th of December",
]
ACME_BAD_IDS = ["um I think it's AC44", "it's on the email somewhere, hang on",
                "A C 4 4 7", "no idea, sorry", "I don't have it with me",
                "AC 9 9 something", "it starts with AC I think", "hang on, I can't find it"]
ACME_HUMAN = ["I want to speak to a real person", "can I talk to a human please",
              "put me through to someone, not a robot",
              "I need to speak to an agent now", "just get me a person please"]
ACME_HUMAN_ANGRY = ["I said I want a human! This is useless.",
                    "Stop it, I'm not giving you anything, get me a real person.",
                    "This is so frustrating, I just want to talk to someone."]
ACME_OPEN = ["what are your opening hours", "do you deliver on Sundays",
             "how much does express cost", "can I pick up from the depot myself",
             "do you ship to Belgium"]


def _close(rng: random.Random, happy: float = 0.5) -> list[str]:
    r = rng.random()
    if r < happy:
        return [_pick(rng, ACME_HAPPY_CLOSE)]
    if r < happy + 0.35:
        return [_pick(rng, ACME_NEUTRAL_CLOSE)]
    return []      # just hangs up


def acme_track(rng: random.Random, fx: Fixtures) -> Script:
    outcome = rng.choices(["in_transit", "delivered", "delayed"], [5, 4, 2])[0]
    sid = _pick(rng, fx.shipments_by(outcome) or list(fx.shipments))
    lines = [_pick(rng, ACME_TRACK), speak_code(rng, sid)]
    if rng.random() < 0.2:    # opener and ID in one breath
        lines = [f"{_pick(rng, ACME_TRACK)}, {sid}"]
    return Script(f"track_{outcome}", lines + _close(rng, 0.55), {"tool"})


def acme_delayed_angry(rng: random.Random, fx: Fixtures) -> Script:
    sid = _pick(rng, fx.shipments_by("delayed") or list(fx.shipments))
    lines = [_pick(rng, ACME_ANGRY_OPEN), speak_code(rng, sid), _pick(rng, ACME_ANGRY_AFTER_DELAY)]
    if rng.random() < 0.4:
        lines.append(_pick(rng, ACME_HUMAN_ANGRY))
    return Script("track_delayed_angry", lines, {"tool"})


def acme_reschedule(rng: random.Random, fx: Fixtures) -> Script:
    sid = _pick(rng, fx.reschedulable() or list(fx.shipments))
    return Script("reschedule_ok", [_pick(rng, ACME_TRACK), speak_code(rng, sid),
                                    _pick(rng, ACME_RESCHEDULE)] + _close(rng, 0.7), {"tool"})


def acme_reschedule_too_late(rng: random.Random, fx: Fixtures) -> Script:
    sid = _pick(rng, fx.too_late() or list(fx.shipments))
    lines = [_pick(rng, ACME_TRACK), speak_code(rng, sid), _pick(rng, ACME_RESCHEDULE)]
    lines.append(rng.choice(["oh no, that's annoying", "seriously? that's really frustrating",
                             "ok, never mind"]))
    return Script("reschedule_too_late", lines, {"tool"})


def acme_not_found_retry(rng: random.Random, fx: Fixtures) -> Script:
    good = _pick(rng, fx.shipments_by("in_transit") + fx.shipments_by("delivered"))
    lines = [_pick(rng, ACME_TRACK), speak_code(rng, unknown_shipment(rng, fx)),
             rng.choice(["sorry, let me check, it's ", "oh wait, I misread it, it's ",
                         "hmm, try "]) + good]
    return Script("not_found_then_found", lines + _close(rng, 0.6), {"tool"})


def acme_not_found_gives_up(rng: random.Random, fx: Fixtures) -> Script:
    lines = [_pick(rng, ACME_TRACK), speak_code(rng, unknown_shipment(rng, fx)),
             rng.choice(["that's the number on my email though, this is so annoying",
                         "well that's what it says, I don't understand",
                         "ugh, forget it"])]
    return Script("not_found_gives_up", lines, {"tool"})


def acme_bad_ids_escalate(rng: random.Random, fx: Fixtures) -> Script:
    """Three unusable answers to the ID prompt: the flow escalates (max_attempts)."""
    lines = [_pick(rng, ACME_TRACK)] + rng.sample(ACME_BAD_IDS, 3)
    return Script("bad_ids_escalated", lines, {"escalation"})


def acme_human_midflow(rng: random.Random, fx: Fixtures) -> Script:
    """Refuses the ID and demands a person until the flow gives up and transfers."""
    lines = [_pick(rng, ACME_TRACK + ACME_ANGRY_OPEN[:2])] + rng.sample(
        ACME_HUMAN + ACME_HUMAN_ANGRY, 3)
    return Script("human_requested_escalated", lines, {"escalation"})


def acme_human_open(rng: random.Random, fx: Fixtures) -> Script:
    """Asks for a person straight away; there is no transfer flow, so they hang up."""
    lines = [_pick(rng, ACME_HUMAN), _pick(rng, ACME_HUMAN_ANGRY)]
    return Script("human_requested_hung_up", lines, set())


def acme_open(rng: random.Random, fx: Fixtures) -> Script:
    return Script("general_question", [_pick(rng, ACME_OPEN)] + _close(rng, 0.4), set())


def acme_bare_id(rng: random.Random, fx: Fixtures) -> Script:
    sid = _pick(rng, list(fx.shipments))
    return Script("bare_id", [rng.choice([f"is {sid}.", f"hi, it's about {sid}", sid])]
                  + _close(rng, 0.5), {"tool"})


# ---- swiftnet ----

SWIFT_FAULT = ["my internet is down", "no connection at all since this morning",
               "the wifi isn't working", "my broadband keeps dropping",
               "my internet is down again", "hi, I have no connection"]
SWIFT_FAULT_ANGRY = ["My internet is down AGAIN, this is the third time this month, I'm furious.",
                     "No connection for two days now, I work from home, this is a disaster!",
                     "My broadband keeps dropping and I'm sick of it."]
SWIFT_TROUBLESHOOT = [
    ["the light on the router is flashing green", "ok I've unplugged it and plugged it back",
     "yes! it's working now, thank you so much"],
    ["the light is solid red", "I restarted it already twice", "still nothing, this is so frustrating"],
    ["there's a green light", "ok, done that", "great, it's back, thanks"],
    ["I've already turned it off and on", "it's still really slow, honestly this is useless"],
    ["the lights are solid green", "ok, unplugged for thirty seconds", "that fixed it, brilliant"],
]
SWIFT_OUTAGE_AFTER = ["ok, thanks for letting me know", "again? that's really annoying",
                      "how long is this going to take, I'm losing work here",
                      "fine, I'll wait"]
SWIFT_SUSPENDED_AFTER = ["Suspended? I paid my bill last week, this is outrageous!",
                         "What? Nobody told me about that, I'm really upset.",
                         "ok, I'll sort the payment out"]
SWIFT_BAD_NUMBER = ["zero one four two eight", "I don't have it on me", "it's oh one something",
                    "hang on, I'm looking", "I think it ends in seven seven"]
SWIFT_FAQ = [
    "what does a flashing green light mean on the router",
    "what does the red light mean",
    "what speed should I get on a fibre package",
    "how do I restart the router",
    "do you support customer owned routers",
    "do you supply wifi extenders",
    "how long does the router take to resync after a restart",
    "is thirty megabits normal on fibre",
]
SWIFT_HUMAN = ["can I speak to an engineer please", "I want to talk to a real person",
               "just put me through to someone"]


def swift_line_ok(rng: random.Random, fx: Fixtures) -> Script:
    num = _pick(rng, fx.lines_by("line_ok") or list(fx.lines))
    opener = _pick(rng, SWIFT_FAULT_ANGRY if rng.random() < 0.3 else SWIFT_FAULT)
    return Script("line_ok_troubleshoot",
                  [opener, speak_code(rng, num)] + list(rng.choice(SWIFT_TROUBLESHOOT)), {"tool"})


def swift_outage(rng: random.Random, fx: Fixtures) -> Script:
    num = _pick(rng, fx.lines_by("area_outage") or list(fx.lines))
    opener = _pick(rng, SWIFT_FAULT_ANGRY if rng.random() < 0.4 else SWIFT_FAULT)
    return Script("area_outage", [opener, speak_code(rng, num), _pick(rng, SWIFT_OUTAGE_AFTER)],
                  {"tool"})


def swift_suspended(rng: random.Random, fx: Fixtures) -> Script:
    num = _pick(rng, fx.lines_by("account_suspended") or list(fx.lines))
    lines = [_pick(rng, SWIFT_FAULT), speak_code(rng, num), _pick(rng, SWIFT_SUSPENDED_AFTER)]
    if rng.random() < 0.5:
        lines.append(_pick(rng, SWIFT_HUMAN))
    return Script("account_suspended", lines, {"tool"})


def swift_not_found(rng: random.Random, fx: Fixtures) -> Script:
    lines = [_pick(rng, SWIFT_FAULT), speak_code(rng, unknown_line(rng, fx))]
    if rng.random() < 0.6:
        good = _pick(rng, fx.lines_by("line_ok") or list(fx.lines))
        lines.append("sorry, wrong number, it's " + speak_code(rng, good))
        lines.append(rng.choice(["the light is solid red", "ok thanks"]))
    else:
        lines.append("that's definitely my number, this is ridiculous")
    return Script("line_not_found", lines, {"tool"})


def swift_bad_number_escalate(rng: random.Random, fx: Fixtures) -> Script:
    return Script("bad_number_escalated", [_pick(rng, SWIFT_FAULT)] + rng.sample(SWIFT_BAD_NUMBER, 3),
                  {"escalation"})


def swift_faq(rng: random.Random, fx: Fixtures) -> Script:
    qs = rng.sample(SWIFT_FAQ, rng.choices([1, 2], [3, 1])[0])
    close = [rng.choice(["ok, thanks", "great, that helps", "perfect, thanks"])] \
        if rng.random() < 0.5 else []
    return Script("faq", qs + close, set())


def swift_human(rng: random.Random, fx: Fixtures) -> Script:
    return Script("human_requested_hung_up",
                  [_pick(rng, SWIFT_HUMAN), "this is useless, I'll call back later"], set())


# ---- stockline: the Stock Control Policy hotline ----

# (question, weight). Covered: the policy answers them.
STOCKLINE_COVERED: list[tuple[str, int]] = [
    ("What is the purpose of the stock control policy?", 2),
    ("What counts as stock under the stock control policy?", 2),
    ("How does the policy define a stock discrepancy?", 3),
    ("Who counts as warehouse staff in the stock control policy?", 1),
    ("Who has to approve a stock transfer?", 5),
    ("Can warehouse staff approve a stock transfer themselves?", 2),
    ("How often do managers review stock discrepancy reports?", 3),
    ("What are sales staff responsible for under the stock policy?", 2),
    ("Who does the monthly stock reconciliation?", 3),
    ("What does a financial analyst do in stock control?", 2),
    ("What are warehouse staff responsible for in stock control?", 2),
    ("What is the receiving procedure for incoming stock?", 4),
    ("How long do I have to record received stock in the system?", 5),
    ("Do I need to record batch numbers when receiving stock?", 3),
    ("What should I check when I inspect incoming goods?", 2),
    ("I received a delivery yesterday and haven't recorded it yet, is that within the time limit?", 2),
    ("What is compliance code SCP-101?", 3),
    ("What happens if someone makes an unauthorized stock adjustment?", 4),
    ("What is the penalty for an unauthorized stock adjustment?", 3),
    ("Does a stock adjustment need dual approval?", 3),
    ("What does compliance code SCP-105 cover?", 2),
    ("What happens if I don't report a stock discrepancy?", 3),
    ("Do you get a warning first or a suspension for an unauthorized stock adjustment?", 2),
    ("What is the target for inventory accuracy?", 4),
    ("What is the maximum acceptable stock loss per month?", 3),
    ("How much of the stock has to be cycle counted?", 2),
    ("What are the KPIs for stock control?", 3),
    ("Is ninety eight percent inventory accuracy good enough?", 1),
    ("Who do I escalate a stock discrepancy to?", 5),
    ("What counts as a minor stock discrepancy?", 3),
    ("What do I do with a stock discrepancy of zero point seven percent?", 2),
    ("When is a stock discrepancy considered major?", 3),
    ("Who needs to be told about a medium stock discrepancy?", 2),
    ("What happens if a stock discrepancy is over one percent?", 2),
    ("When does a financial analyst get involved in a stock discrepancy?", 2),
    ("How often is stock control training?", 2),
    ("Are there monthly refresher trainings on stock control?", 2),
    ("Is the annual stock control training mandatory?", 2),
    ("How long do we keep stock records?", 4),
    ("How often are stock audits done?", 3),
    ("Are the stock audits independent?", 1),
    ("Can I throw away stock records from five years ago?", 1),
]

# Not in the policy at all: what the gaps list should surface. The first few come
# up again and again, so the list has a clear top.
STOCKLINE_UNCOVERED: list[tuple[str, int]] = [
    ("How do I reset my password?", 16),
    ("When do we get paid for overtime?", 10),
    ("What is the holiday schedule for Christmas?", 8),
    ("Where can I park my car?", 5),
    ("How do I get my payslip?", 5),
    ("How do I report sick leave?", 4),
    ("How do I connect to the VPN from home?", 3),
    ("Where do I get a new uniform?", 3),
    ("How do I renew my forklift licence?", 3),
    ("Can I swap shifts with a colleague?", 3),
    ("How many days of annual leave do I have left?", 2),
    ("What time does the night shift start?", 2),
    ("My badge isn't working, who do I call?", 2),
    ("How do I claim travel expenses?", 2),
    ("What's the wifi password for the warehouse?", 2),
    ("How do I order new safety boots?", 2),
    ("How do I update my bank details for payroll?", 2),
    ("Do we get paid extra for working bank holidays?", 2),
    ("What's on the canteen menu today?", 1),
    ("Who do I talk to about a pay rise?", 1),
    ("Is there a staff discount?", 1),
    ("How do I book a meeting room?", 1),
    ("Where is the first aid kit?", 1),
    ("Can I bring my dog to work?", 1),
    ("When is the next fire drill?", 1),
    ("Who is the union representative?", 1),
]

UNCOVERED_SHARE = 0.30      # of questions asked (target: 25-35%)

# Emotion carried by the wording. Covered questions get a prefix most of the
# time; uncovered ones rarely, so the same gap is asked in the same words.
STOCKLINE_PREFIX = {
    "confused": ["Sorry, I'm a bit confused. ", "I'm new here and not sure about this. ",
                 "I don't really understand the rules here. "],
    "anxious": ["I'm worried I've made a mistake. ", "I'm a bit nervous about this, ",
                "I'm scared I'll get in trouble. "],
    "neutral": ["", "", "Quick question. ", "Hi, "],
}
STOCKLINE_CLOSE = {
    "satisfied": ["Perfect, that's really helpful, thank you.", "Great, that answers it, thanks.",
                  "Brilliant, thanks a lot.", "Oh good, that's a relief. Thank you.",
                  "Lovely, cheers."],
    "frustrated": ["That doesn't help me at all.", "So nobody can tell me that? Great.",
                   "Useless. I'll ask my supervisor.", "Honestly, what's the point of this line."],
}
# Every utterance is a search, closers included, and a closer finds nothing in the
# policy. Kept rare and varied, so they do not crowd the real gaps off the list.


def stockline_question(rng: random.Random) -> tuple[str, bool]:
    """(question as spoken, covered?)."""
    if rng.random() < UNCOVERED_SHARE:
        q = _weighted(rng, STOCKLINE_UNCOVERED)
        if rng.random() < 0.1:
            q = _prefixed(_pick(rng, STOCKLINE_PREFIX["neutral"][2:]), q)
        return q, False
    q = _weighted(rng, STOCKLINE_COVERED)
    mood = rng.choices(["neutral", "confused", "anxious"], [5, 3, 2])[0]
    return _prefixed(_pick(rng, STOCKLINE_PREFIX[mood]), q), True


def _prefixed(prefix: str, q: str) -> str:
    """'Hi, ' + 'How do I...' -> 'Hi, how do I...'; a sentence prefix keeps the capital."""
    if prefix.endswith(", ") and not q.startswith(("I ", "I'")):
        return prefix + q[0].lower() + q[1:]
    return prefix + q


def _weighted(rng: random.Random, pool: list[tuple[str, int]]) -> str:
    return rng.choices([q for q, _ in pool], [w for _, w in pool])[0]


def stockline_call(rng: random.Random, fx: Fixtures) -> Script:
    n = rng.choices([1, 2, 3], [5, 3.5, 1.5])[0]
    asked = [stockline_question(rng) for _ in range(n)]
    lines = [q for q, _ in asked]
    covered = sum(c for _, c in asked)
    r = rng.random()
    if r < 0.07 and covered:
        lines.append(_pick(rng, STOCKLINE_CLOSE["satisfied"]))
    elif r < 0.10 and covered < n:
        lines.append(_pick(rng, STOCKLINE_CLOSE["frustrated"]))
    name = "policy_questions" if covered == n else "includes_uncovered"
    return Script(name, lines, {"search"})


# -- the scenario table ------------------------------------------------------------

# tenant -> [(builder, weight)]. Weights are relative within a tenant.
SCENARIOS: dict[str, list[tuple[Builder, float]]] = {
    "acme": [
        (acme_track, 26), (acme_delayed_angry, 8), (acme_reschedule, 14),
        (acme_reschedule_too_late, 4), (acme_not_found_retry, 6), (acme_not_found_gives_up, 4),
        (acme_bad_ids_escalate, 7), (acme_human_midflow, 5), (acme_human_open, 4),
        (acme_open, 6), (acme_bare_id, 5),
    ],
    "swiftnet": [
        (swift_line_ok, 24), (swift_outage, 12), (swift_suspended, 6), (swift_not_found, 6),
        (swift_bad_number_escalate, 6), (swift_faq, 18), (swift_human, 4),
    ],
    "stockline": [(stockline_call, 1)],
}


def pick_script(tenant: str, rng: random.Random, fx: Fixtures,
                boost: dict[str, float] | None = None) -> Script:
    """One caller for `tenant`. `boost` multiplies a builder's weight (by its name),
    e.g. an outage day: {"swift_outage": 4}."""
    table = SCENARIOS[tenant]
    weights = [w * (boost or {}).get(b.__name__, 1.0) for b, w in table]
    builder = rng.choices([b for b, _ in table], weights)[0]
    return builder(rng, fx)


# -- traffic shape -------------------------------------------------------------------

# Relative volume by weekday, Monday first.
WEEKDAY_FACTOR = {
    "acme":      [1.00, 1.08, 1.00, 0.97, 0.93, 0.45, 0.22],
    "swiftnet":  [1.00, 0.95, 0.95, 0.97, 1.00, 0.75, 0.62],   # home broadband: weekends too
    "stockline": [1.05, 1.00, 1.00, 1.00, 0.95, 0.18, 0.08],   # staff hotline: shift days
}

# Relative volume by local hour (0-23), weekdays. Lunch dip at 12-13.
_OFFICE = [0, 0, 0, 0, 0, 0, 0.05, 0.3, 1.2, 2.4, 2.8, 2.6, 1.3, 1.4, 2.4, 2.5, 2.3, 1.8,
           0.8, 0.4, 0.2, 0.1, 0.03, 0]
HOUR_WEIGHTS = {
    "acme": _OFFICE,
    # Evenings matter for a home broadband line.
    "swiftnet": [0, 0, 0, 0, 0, 0, 0.05, 0.3, 1.0, 2.0, 2.3, 2.2, 1.3, 1.4, 2.0, 2.1, 2.1, 2.0,
                 2.2, 2.4, 2.0, 1.2, 0.4, 0.1],
    # Warehouse shifts start at 6 and 14: a bump after each.
    "stockline": [0, 0, 0, 0, 0, 0, 0.8, 1.8, 2.0, 2.4, 2.5, 2.2, 1.1, 1.2, 2.2, 2.3, 2.0, 1.5,
                  0.8, 0.5, 0.4, 0.2, 0.1, 0],
}
# Weekends are flatter: mid-morning to late afternoon.
_WEEKEND = [0, 0, 0, 0, 0, 0, 0, 0.1, 0.5, 1.4, 2.0, 2.1, 1.6, 1.5, 1.8, 1.7, 1.4, 1.0, 0.6,
            0.4, 0.3, 0.2, 0.05, 0]

DEFAULT_CALLS_PER_DAY = {"acme": 50, "swiftnet": 40, "stockline": 34}


def hour_weights(tenant: str, weekday: int) -> list[float]:
    if weekday >= 5 and tenant != "stockline":
        return list(_WEEKEND)
    return list(HOUR_WEIGHTS[tenant])


def calls_on_day(rng: random.Random, tenant: str, weekday: int, mean_weekday: float,
                 trend: float = 1.0) -> int:
    """How many calls a tenant gets on one day: weekday shape, a trend, some noise."""
    mean = mean_weekday * WEEKDAY_FACTOR[tenant][weekday] * trend
    return max(0, round(mean * rng.lognormvariate(0, 0.12)))


# -- the policy itself, for offline runs ------------------------------------------------
#
# The real run searches the document uploaded to the platform, through the real
# knowledge service. Offline runs and the tests search this stand-in instead
# (history.OfflineKnowledge): the same facts, section by section.

STOCK_CONTROL_POLICY_TITLE = "Stock Control Policy"
STOCK_CONTROL_POLICY: list[tuple[str, str]] = [
    ("Purpose", "This policy sets out how stock is received, recorded, moved, counted and "
                "reconciled, so that stock records stay accurate and stock losses are kept low."),
    ("Definitions", "Stock means all goods held for sale or use. Warehouse staff are the "
                    "employees who receive, store, pick and move stock. A stock discrepancy is "
                    "any difference between the stock recorded in the system and the physical "
                    "stock counted."),
    ("Roles and responsibilities", "Warehouse staff receive, store and count stock and report "
                                   "every discrepancy. Sales staff check stock availability before "
                                   "confirming orders and never adjust stock. Managers approve "
                                   "stock transfers and review discrepancy reports weekly. "
                                   "Financial analysts perform the monthly stock reconciliation."),
    ("Receiving procedure", "Inspect incoming goods for damage and check them against the "
                            "delivery note. Record received stock in the system within 24 hours, "
                            "including the batch numbers."),
    ("Compliance codes", "SCP-101 Unauthorized stock adjustment: every stock adjustment needs "
                         "dual approval; a first breach brings a written warning, a repeated "
                         "breach a suspension. SCP-105 Reporting violations: failing to report a "
                         "stock discrepancy or reporting it late."),
    ("Key performance indicators", "Inventory accuracy of at least 99% every month. Stock loss "
                                   "of no more than 0.5% per month. Cycle count coverage of 100% "
                                   "of stock locations."),
    ("Escalation thresholds", "A minor discrepancy, below 0.5%, is handled by warehouse staff "
                              "and their manager. A medium discrepancy, between 0.5% and 1%, is "
                              "escalated to the manager and a financial analyst. A major "
                              "discrepancy, above 1%, is escalated to senior management."),
    ("Training", "All staff complete stock control training annually, with monthly refresher "
                 "sessions."),
    ("Records and audits", "Stock records are kept for 7 years. Independent stock audits are "
                           "carried out quarterly."),
]
