"""One turn of conversation: flow engine first, LLM only where it's needed.

Still a separate module from the media edge because this is the ORCHESTRATOR's
job. It lives here until that service exists, so moving it later is a relocation.

Turn shapes, cheapest first:

  scripted   slot prompt / reprompt   literal config text -> TTS       ~0.6 s
  grounded   tool outcome             LLM phrases tool data -> TTS     ~1.6 s
  open       no flow matched          LLM answers from knowledge       ~1.6 s

The scripted path skips the LLM entirely. That is both faster and deterministic —
the shipment-ID question is worded identically on every call, which is what you
want from a process step.
"""

from __future__ import annotations

import json
import logging
import re
import time

import aiohttp

from .flow import CallFlowState, Tenant, TurnAction, route
from .tools import ToolRegistry

log = logging.getLogger("agent")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_BOUNDARY = re.compile(r'(?<=[.!?])\s+|(?<=[,;:])\s+(?=\w{6,})')
MIN_CLAUSE_CHARS = 18
MAX_CLAUSE_CHARS = 160


class Agent:
    """One conversation for one tenant."""

    def __init__(self, api_key: str, model: str, tenant: Tenant,
                 tools: ToolRegistry):
        self.api_key = api_key
        self.model = model
        self.tenant = tenant
        self.tools = tools
        self.history: list[dict] = []
        self.state = CallFlowState(tenant)
        self.cancelled = False
        self.ended = False          # escalated: stop talking

    # -- prompts ----------------------------------------------------------

    def _system(self, extra: str = "") -> str:
        parts = [self.tenant.persona.strip()]
        parts.append(
            "You are speaking out loud on a phone call. Keep replies to one or two "
            "short sentences. No markdown, no lists, no emoji."
        )
        if self.tenant.guardrails:
            parts.append("Rules you must follow:\n" +
                         "\n".join(f"- {g}" for g in self.tenant.guardrails))
        if self.tenant.knowledge_text:
            parts.append(
                "Reference material. Answer only from this; if it is not here, say so.\n"
                + self.tenant.knowledge_text
            )
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)

    # -- LLM --------------------------------------------------------------

    async def _stream_llm(self, session: aiohttp.ClientSession, messages: list[dict]):
        payload = {"model": self.model, "messages": messages, "temperature": 0.5,
                   "max_tokens": 180, "stream": True}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        async with session.post(GROQ_URL, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=45)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"LLM HTTP {resp.status}: {(await resp.text())[:160]}")
            async for raw in resp.content:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if tok := delta.get("content"):
                    yield tok

    # -- speaking ---------------------------------------------------------
    #
    # "Speaking" here means emitting text clauses. Synthesis happens on the data
    # plane: the orchestrator never handles audio. Clauses go out as they become
    # available because Magpie does not stream — a whole reply would be seconds
    # of silence, where the first clause lands in ~0.6s.

    def _emit_clauses(self, text: str, on_say):
        for clause in list(self._drain(text)) or [text]:
            if self.cancelled:
                return
            on_say(clause.strip())

    # -- greeting ---------------------------------------------------------

    async def greet(self, on_event, on_say):
        text = (self.tenant.voice or {}).get("greeting")
        if not text:
            return
        on_event({"type": "token", "text": text})
        self._emit_clauses(text, on_say)
        on_event({"type": "turn_complete", "total_s": 0.0, "text": text})

    # -- a turn -----------------------------------------------------------

    async def respond(self, user_text: str, on_event, on_say):
        self.cancelled = False
        t0 = time.perf_counter()

        action = self._decide(user_text)
        on_event({"type": "flow", "kind": action.kind,
                  "intent": action.flow.intent if action.flow else None,
                  "slots": dict(self.state.filled)})
        if action.next_silence_ms:
            # Slot-driven endpointing: a code read aloud needs a longer window, or
            # STT cuts the caller off mid-sequence.
            on_event({"type": "set_silence_ms", "value": action.next_silence_ms})

        async with aiohttp.ClientSession() as session:
            try:
                if action.kind == "say":
                    # Scripted: literal config text, no LLM call at all.
                    on_event({"type": "token", "text": action.text})
                    self._emit_clauses(action.text, on_say)
                    spoken = action.text

                elif action.kind == "escalate":
                    on_event({"type": "token", "text": action.text})
                    self._emit_clauses(action.text, on_say)
                    on_event({"type": "escalated"})
                    spoken = action.text
                    self.state.reset_flow()
                    # A live test showed the agent carrying on for 17 turns after
                    # escalating, improvising a diagnosis it had never performed.
                    self.ended = True

                else:
                    spoken = await self._llm_turn(session, user_text, action,
                                                  on_event, on_say)

            except Exception as exc:
                log.exception("turn failed")
                on_event({"type": "error", "detail": str(exc)[:200]})
                return

        if spoken:
            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": spoken})
            self.history = self.history[-20:]
        on_event({"type": "turn_complete", "total_s": round(time.perf_counter() - t0, 2),
                  "text": spoken})

    def _decide(self, user_text: str) -> TurnAction:
        """Pure flow logic — no I/O, no model."""
        if self.state.active:
            action = self.state.advance(user_text)
            if action.kind != "ready_for_tool":
                return action
            return TurnAction(kind="ready_for_tool", flow=self.state.active)

        flow = route(self.tenant, user_text)
        if flow is None:
            return TurnAction(kind="open")
        if flow.requires_flow and flow.requires_flow not in self.state.completed:
            flow = next((f for f in self.tenant.flows
                         if f.intent == flow.requires_flow), flow)
        return self.state.begin(flow)

    async def _llm_turn(self, session, user_text, action, on_event, on_say):
        """Grounded (tool result) or open (knowledge) — both need phrasing."""
        extra = ""

        if action.kind == "ready_for_tool" and action.flow and action.flow.tool:
            result = await self.tools.call(action.flow.tool, dict(self.state.filled))
            outcome = result.get("outcome", "error")
            on_event({"type": "tool_result", "tool": action.flow.tool,
                      "outcome": outcome, "data": result})

            spec = action.flow.outcomes.get(outcome)
            guidance = spec.get("guidance") if isinstance(spec, dict) else (spec or "")
            if not guidance:
                guidance = ("Explain that the lookup did not succeed. "
                            "Do not invent a result.")
            extra = (
                f"The {action.flow.tool} tool returned this data:\n"
                f"{json.dumps(result, ensure_ascii=False)}\n\n"
                f"How to respond: {guidance}\n"
                "Use only the values above. Never invent a date, status or figure."
            )
            self.state.completed.add(action.flow.intent)
            self.state.reset_flow()

        if action.kind == "open" and self.tenant.flows:
            # THE IMPORTANT GUARD. With no flow matched, nothing has been looked
            # up — no tool ran, no slot was filled. Left unconstrained the model
            # will happily simulate the process from the persona: a live test had
            # it walking troubleshooting steps and concluding "this looks like a
            # line fault" without check_line_status ever being called.
            starters = "; ".join(
                f"{f.intent} (e.g. \"{f.utterances[0]}\")"
                for f in self.tenant.flows if not f.internal and f.utterances
            )
            extra = (
                "IMPORTANT: you have not looked anything up. No system check has "
                "run and you have no data about this caller.\n"
                "- Do NOT diagnose, troubleshoot, or give step-by-step instructions.\n"
                "- Do NOT claim to have checked, tested or found anything.\n"
                "- Do NOT state or imply a cause, status or outcome.\n"
                f"If the caller wants one of these, ask the question that starts it: {starters}.\n"
                "Otherwise answer only from the reference material above, or say you "
                "cannot help with that and offer to transfer."
            )

        messages = ([{"role": "system", "content": self._system(extra)}]
                    + self.history + [{"role": "user", "content": user_text}])

        spoken, pending = "", ""
        async for tok in self._stream_llm(session, messages):
            if self.cancelled:
                break
            spoken += tok
            pending += tok
            on_event({"type": "token", "text": tok})
            for clause in self._drain(pending):
                pending = pending[len(clause):]
                if self.cancelled:
                    break
                on_say(clause.strip())

        tail = pending.strip()
        if tail and not self.cancelled:
            on_say(tail)
        return spoken.strip()

    @staticmethod
    def _drain(buf: str):
        while True:
            m = next((c for c in _BOUNDARY.finditer(buf) if c.end() >= MIN_CLAUSE_CHARS), None)
            if m:
                yield buf[:m.end()]
                buf = buf[m.end():]
                continue
            if len(buf) >= MAX_CLAUSE_CHARS:
                cut = buf.rfind(" ", 0, MAX_CLAUSE_CHARS)
                cut = cut if cut > MIN_CLAUSE_CHARS else MAX_CLAUSE_CHARS
                yield buf[:cut]
                buf = buf[cut:]
                continue
            return
