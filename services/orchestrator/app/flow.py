"""Tenant config loading and the flow state machine.

This is where "prompts request, the orchestrator enforces" becomes code. The LLM
never decides whether a required slot has been satisfied — a regex does, and the
flow refuses to advance until it matches.

Three kinds of turn, and only one of them needs the LLM:

  scripted   slot prompt / reprompt / on_invalid   -> literal text from config,
                                                      straight to TTS. No LLM call,
                                                      so ~600 ms instead of ~1.6 s.
  grounded   tool outcome                          -> LLM phrases the tool's data.
  open       no flow matched                       -> LLM answers from knowledge.

Skipping the LLM on scripted turns is a real latency win *and* makes the demo
deterministic: the shipment-ID question comes out identically every time.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .dates import parse_date

log = logging.getLogger("flow")

# Repo layout by default; containers point this at a mounted config directory.
TENANTS_DIR = Path(os.environ.get("TENANTS_DIR")
                   or Path(__file__).resolve().parents[3] / "tenants")

# Callers read codes aloud and STT writes them as words as often as digits.
_NUMBER_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "ate": "8", "nine": "9",
}


# "double seven" / "triple four" — how people actually read numbers aloud.
_REPEAT = {"double": 2, "triple": 3, "treble": 3}


def normalise_spoken(text: str) -> str:
    """'A C four four seven one X two zero nine' -> 'AC4471X209'.

    Also expands 'double seven' -> '77'. A live test had a caller say
    "0, 1, 4, 2, double hit, double seven, double one" and burn all three slot
    attempts, because every 'double N' was dropped on the floor.
    """
    words = [w for w in re.split(r"[\s,.\-_]+", text.strip()) if w]
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i].lower().strip(".,!?")
        rep = _REPEAT.get(w)
        if rep and i + 1 < len(words):
            nxt = words[i + 1].lower().strip(".,!?")
            digit = _NUMBER_WORDS.get(nxt, nxt if nxt.isdigit() else None)
            if digit is not None:
                out.append(digit * rep)
                i += 2
                continue
        out.append(_NUMBER_WORDS.get(w, words[i]))
        i += 1
    return "".join(out)


def _code_like(token: str) -> bool:
    """Can this token be part of a spoken code?

    Only single characters ("A", "C", "7"), number/repeat words ("four",
    "double") and chunks containing a digit ("AC4471", "X209") qualify. Ordinary
    words — "it", "is", "okay", "think" — do not, which is what stops them being
    glued onto a code and passing the pattern by accident.
    """
    t = re.sub(r"[^A-Za-z0-9]", "", token).lower()
    if not t:
        return False
    if len(t) == 1:
        return True
    if t in _NUMBER_WORDS or t in _REPEAT:
        return True
    return any(c.isdigit() for c in t)


@dataclass
class Slot:
    name: str
    pattern: str
    prompt: str
    reprompt: str
    on_invalid: str = ""
    max_attempts: int = 3
    type: str = "text"
    silence_ms: int | None = None
    # Date slots: the known fact a relative date counts from, e.g. the current
    # estimated_delivery for "push it back three days".
    relative_to: str | None = None

    def extract(self, utterance: str, facts: dict[str, Any] | None = None,
                today: date | None = None) -> str | None:
        """Pull a valid value out of what the caller said, or None.

        Date slots parse spoken dates (app/dates.py) and return ISO 8601; `facts`
        and `today` are what relative dates count from. Every other slot type is
        matched against its pattern, as below.

        Callers say "it's AC4471X209" or "A C four four seven one X two zero nine",
        so we try the raw text, the spoken-form normalisation, and word-bounded
        spans. Doing this with a regex rather than an LLM call keeps the turn free.

        The guard below matters: an earlier version searched for the pattern
        ANYWHERE in the normalised text, which matched ten letters out of the
        middle of "um I do not know" and happily looked it up.
        """
        if self.type == "date":
            return self._extract_date(utterance, facts or {}, today or date.today())

        rx = re.compile(self.pattern)
        raw = utterance.strip()

        def ok(cand: str) -> bool:
            if not rx.match(cand):
                return False
            # A code slot must contain a digit. Prose never does, so this alone
            # kills the whole class of false positives.
            if self.type in ("digits", "alphanumeric") and not any(c.isdigit() for c in cand):
                return False
            return True

        # 1. the whole utterance is the value
        if ok(raw):
            return raw

        # 2. spoken digits collapse to the value
        norm = normalise_spoken(raw)
        if ok(norm):
            return norm

        # 3. embedded in a sentence, written form — anchored to word boundaries
        #    so it cannot slice through the middle of a longer word
        core = self.pattern.strip("^$")
        for m in re.finditer(rf"\b{core}\b", raw):
            if ok(m.group(0)):
                return m.group(0)

        # 4. embedded, spoken out across several tokens:
        #    "it is A C four four seven one X two zero nine okay"
        #
        # Only build spans from unbroken runs of code-like tokens. Searching
        # every span let filler be absorbed from either end: left-to-right gave
        # "it is A C four four seven one" -> "itisAC4471", and preferring the
        # latest start instead gave "seven one X two zero nine okay" ->
        # "71X209okay". Both are ten alphanumerics containing digits, so both
        # passed the pattern and the digit guard. Excluding ordinary words from
        # the span entirely removes the whole class.
        #
        # Later runs win: callers put filler before the code, not after.
        tokens = raw.split()
        runs: list[list[str]] = []
        current: list[str] = []
        for tok in tokens:
            if _code_like(tok):
                current.append(tok)
            elif current:
                runs.append(current)
                current = []
        if current:
            runs.append(current)

        for run in reversed(runs):
            for i in range(len(run)):
                for j in range(len(run), i, -1):   # longest span first
                    cand = normalise_spoken(" ".join(run[i:j]))
                    if ok(cand):
                        return cand
        return None

    def _extract_date(self, utterance: str, facts: dict[str, Any],
                      today: date) -> str | None:
        base = None
        ref = facts.get(self.relative_to) if self.relative_to else None
        if isinstance(ref, str):
            try:
                base = date.fromisoformat(ref[:10])
            except ValueError:
                base = None
        d = parse_date(utterance, today=today, base=base)
        return d.isoformat() if d else None


@dataclass
class Flow:
    intent: str
    utterances: list[str] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    tool: str | None = None
    outcomes: dict[str, Any] = field(default_factory=dict)
    internal: bool = False
    requires_flow: str | None = None
    steps: list[str] = field(default_factory=list)


@dataclass
class Tenant:
    tenant_id: str
    display_name: str
    slug: str
    locale: str
    voice: dict
    persona: str
    endpointing: dict
    flows: list[Flow]
    tools: list[str]
    knowledge_text: str
    guardrails: list[str]
    escalation: dict

    @property
    def language(self) -> str:
        return self.locale.split("-")[0]


def load_tenants(directory: Path = TENANTS_DIR) -> dict[str, Tenant]:
    """slug -> Tenant. The slug is what the widget URL carries."""
    tenants: dict[str, Tenant] = {}
    for path in sorted(directory.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text())

        flows = []
        for f in cfg.get("flows") or []:
            slots = []
            for s in f.get("slots") or []:
                slots.append(Slot(
                    name=s["name"], pattern=s.get("pattern", ""), prompt=s["prompt"],
                    reprompt=s.get("reprompt", s["prompt"]),
                    on_invalid=s.get("on_invalid", ""),
                    max_attempts=s.get("max_attempts", 3),
                    type=s.get("type", "text"),
                    silence_ms=(s.get("endpointing") or {}).get("silence_ms"),
                    relative_to=s.get("relative_to"),
                ))
            flows.append(Flow(
                intent=f["intent"], utterances=f.get("utterances") or [], slots=slots,
                tool=f.get("tool"), outcomes=f.get("outcomes") or {},
                internal=f.get("internal", False),
                requires_flow=f.get("requires_flow"), steps=f.get("steps") or [],
            ))

        kn = cfg.get("knowledge") or {}
        text = ""
        for src in kn.get("sources") or []:
            p = directory / src
            if p.exists():
                text += f"\n\n--- {p.name} ---\n{p.read_text()}"

        t = Tenant(
            tenant_id=cfg["tenant_id"], display_name=cfg["display_name"],
            slug=cfg["slug"], locale=cfg["locale"], voice=cfg.get("voice") or {},
            persona=cfg.get("persona", ""), endpointing=cfg.get("endpointing") or {},
            flows=flows, tools=cfg.get("tools") or [], knowledge_text=text.strip(),
            guardrails=cfg.get("guardrails") or [], escalation=cfg.get("escalation") or {},
        )
        tenants[t.slug] = t
        log.info("tenant %s (%s): %d flow(s), %d tool(s), %d chars of knowledge",
                 t.slug, t.locale, len(flows), len(t.tools), len(t.knowledge_text))
    return tenants


def route(tenant: Tenant, utterance: str) -> Flow | None:
    """Pick a flow by keyword overlap with its example utterances.

    Deliberately cheap — no embedding, no LLM classification call, because either
    would add latency to every single turn. Good enough for two flows per tenant;
    a real deployment wants a proper intent classifier, and that is a known gap.
    """
    words = set(re.findall(r"\w+", utterance.lower()))
    best, best_score = None, 0.0
    for f in tenant.flows:
        if f.internal:
            continue
        for ex in f.utterances:
            ex_words = set(re.findall(r"\w+", ex.lower()))
            if not ex_words:
                continue
            score = len(words & ex_words) / len(ex_words)
            if score > best_score:
                best, best_score = f, score
    return best if best_score >= 0.4 else None


def route_by_slot(tenant: Tenant, utterance: str,
                  state: CallFlowState | None = None) -> Flow | None:
    """Fallback when the keywords miss: route on a valid value for a flow's first slot.

    A caller who just says "it's AC4471X209" wants that parcel tracked, and STT
    regularly mangles the routing words themselves (a live call heard "where is
    my parcel" as "may locate the parcel"). Without this, the call stays in open
    conversation, where no tool can run, and the LLM can only stall.
    Likewise, once a parcel has been looked up, a bare date ("the third of
    October") starts the reschedule. A flow that requires another is considered
    only once that one has completed; its value alone would not prove it.
    The slot checked is the first one the call does not already know.
    """
    completed = state.completed if state else set()
    for f in tenant.flows:
        if f.internal or not f.slots:
            continue
        if f.requires_flow and f.requires_flow not in completed:
            continue
        inherited = state.inherited(f) if state else {}
        slot = next((s for s in f.slots if s.name not in inherited), None)
        if slot is None:
            continue
        facts = state.facts if state else {}
        today = state.today if state else None
        if slot.extract(utterance, facts, today):
            return f
    return None


@dataclass
class TurnAction:
    """What the flow decided. `kind` tells the agent how much work to do."""
    kind: str                       # say | tool_result | open | escalate
    text: str = ""                  # for `say`: literal, skip the LLM entirely
    flow: Flow | None = None
    outcome: str = ""
    data: dict = field(default_factory=dict)
    guidance: str = ""
    next_silence_ms: int | None = None


class CallFlowState:
    """Per-call flow position. Cheap: a pointer, a dict of slots, attempt counts."""

    def __init__(self, tenant: Tenant):
        self.tenant = tenant
        self.active: Flow | None = None
        self.filled: dict[str, str] = {}
        self.attempts: dict[str, int] = {}
        self.completed: set[str] = set()
        # What earlier flows established: their slot values and their tool's data.
        # A flow that requires another reuses them, so rescheduling does not ask
        # for the shipment ID the caller gave a minute ago.
        self.facts: dict[str, Any] = {}
        self.today: date | None = None      # None = the real date; tests pin it

    def reset_flow(self):
        self.active = None
        self.filled = {}
        self.attempts = {}

    def complete(self, tool_data: dict[str, Any] | None = None):
        """The active flow's tool has run: remember what it established, then reset."""
        if self.active:
            self.completed.add(self.active.intent)
            self.facts.update(self.filled)
            self.facts.update({k: v for k, v in (tool_data or {}).items()
                               if isinstance(v, (str, int, float, bool))})
        self.reset_flow()

    def inherited(self, flow: Flow) -> dict[str, str]:
        """Slot values a flow takes from earlier in the call. Only a flow that
        requires another inherits: tracking a second parcel must still ask."""
        if not flow.requires_flow:
            return {}
        return {s.name: str(self.facts[s.name]) for s in flow.slots
                if s.name in self.facts}

    def _pending_slot(self) -> Slot | None:
        if not self.active:
            return None
        for s in self.active.slots:
            if s.name not in self.filled:
                return s
        return None

    def begin(self, flow: Flow, utterance: str = "") -> TurnAction:
        """Enter a flow. If the utterance that started it already carries the first
        slot's value ("where is my parcel, AC4471X209"), take it instead of asking."""
        self.active = flow
        self.filled = self.inherited(flow)
        self.attempts = {}
        slot = self._pending_slot()
        if slot and utterance and slot.extract(utterance, self.facts, self.today):
            return self.advance(utterance)
        if slot:
            return TurnAction(kind="say", text=slot.prompt, flow=flow,
                              next_silence_ms=slot.silence_ms)
        return TurnAction(kind="ready_for_tool", flow=flow)

    def advance(self, utterance: str) -> TurnAction:
        """Feed one caller utterance into the active flow."""
        slot = self._pending_slot()
        if slot is None:
            return TurnAction(kind="ready_for_tool", flow=self.active)

        value = slot.extract(utterance, self.facts, self.today)
        if value:
            self.filled[slot.name] = value
            log.info("slot %s filled: %r", slot.name, value)
            nxt = self._pending_slot()
            if nxt:
                return TurnAction(kind="say", text=nxt.prompt, flow=self.active,
                                  next_silence_ms=nxt.silence_ms)
            return TurnAction(kind="ready_for_tool", flow=self.active)

        # Invalid. Count it, and give up rather than loop forever.
        n = self.attempts.get(slot.name, 0) + 1
        self.attempts[slot.name] = n
        log.info("slot %s attempt %d/%d failed on %r",
                 slot.name, n, slot.max_attempts, utterance[:40])
        if n >= slot.max_attempts:
            return TurnAction(kind="escalate", flow=self.active,
                              text=self.tenant.escalation.get(
                                  "message", "Let me put you through to someone."))
        return TurnAction(kind="say", text=slot.on_invalid or slot.reprompt,
                          flow=self.active, next_silence_ms=slot.silence_ms)
