# Tenants

Per-company configuration. **Three demo tenants, deliberately different**, so the
demo's claim — *same platform, different behaviour, config only* — is visible rather
than asserted.

```bash
../.venv/bin/python validate.py
```

| tenant | slug | locale | shows |
| --- | --- | --- | --- |
| Acme Delivery | `acme` | en-US | **Enforced process.** Can't talk past the shipment ID; malformed IDs are rejected and re-asked |
| SwiftNet | `swiftnet` | en-US | **Different slot type + branching.** Digits not alphanumerics; one tool, four different conversations after it |
| Maison Verte | `maisonverte` | **fr-FR** | **The other path entirely.** No slots, answers from documents, and the whole stack in French |

The French one is the cheapest big differentiator we have: STT takes a language
param and Magpie's French measured character-perfect in `spikes/magpie-tts/`. It
answers "is this just a scripted bot" without a word of argument.

## The three things a tenant config carries

Keeping these separate is the main design decision here:

| | example | who enforces it |
| --- | --- | --- |
| **Knowledge** | refund policy, plant care, router lights | LLM, from prompt or retrieval |
| **Process** | "shipment ID before status" | **the orchestrator — deterministically** |
| **Tools** | `lookup_shipment` | orchestrator, from a per-tenant allowlist |

**Prompts request; the orchestrator enforces.** A prompt saying "always ask for the
shipment ID first" is obeyed *most* of the time, and "most of the time" is how you
quote a delivery date to someone who never proved they own the parcel. The `slots:`
block is a gate, not a suggestion — the LLM does the talking, but the flow does not
advance until every required slot is filled and matches its `pattern`.

## Schema

```yaml
tenant_id / display_name / slug / locale
voice:        speaker (Magpie: Aria|Jason|John|Leo|Sofia), greeting
persona:      system prompt
endpointing:  silence_ms, min_speech_ms          # tenant defaults
flows:
  - intent, utterances                           # routing examples
    slots:                                       # ENFORCED
      name, type, pattern, prompt, reprompt,
      on_invalid, max_attempts,
      endpointing: {silence_ms}                  # per-slot override
    tool                                         # must be in tools:
    outcomes: {<tool result>: guidance}          # LLM rewrites, cannot invent
    requires_flow / then_flow / internal
tools:        allowlist — enforced server-side from the session's tenant
knowledge:    namespace, sources, strategy (in_prompt | retrieval)
guardrails / escalation
```

### Why slots carry their own endpointing

`silence_ms` is 900 by default, but a slot read *aloud* — a ten-character shipment
ID, a phone number — needs ~1400 or callers get cut off mid-code. This is the
policy-pushed-down-as-parameters pattern already in `services/stt/proto/stt.proto`:
the orchestrator owns the number, STT applies it, per stream. `validate.py` warns if
a `digits`/`alphanumeric` slot leaves it below 1200.

## Fixtures

`fixtures/shipments.json` and `fixtures/lines.json` are **fake tool backends**. Real
integrations are weeks of work and add nothing to the demo story.

Both include the failure cases on purpose — a demo that only walks the happy path
proves nothing. Useful IDs:

| | |
| --- | --- |
| `AC4471X209` | in transit |
| `AC8823B110` | delivered, signed |
| `AC1199Z044` | **delayed** — apology + new estimate + escalation offer |
| anything else | **not_found** — shows it refusing to speculate |
| `0142887711` | line OK → walks the troubleshooting steps |
| `0298445102` | **area outage** → does *not* troubleshoot, gives ETA |
| `0155093388` | **suspended** → transfers, won't discuss the amount |

## On visual flow builders

The schema is the source of truth; a visual editor is a **view over it**. Pick a tool
first and you inherit its data model permanently.

**BPMN is the wrong abstraction for dialogue.** It's built for long-running
multi-actor business processes — approvals, fulfilment, human tasks, compensation
spanning days. It has no native notion of *"the caller said something ambiguous"*:
re-prompting, partial slots, mid-sentence corrections, barge-in — which is most of
what a conversation actually is. The engines also add latency to a loop where we're
fighting for 100 ms.

Where BPMN *does* fit this product: the **back-office process after the call** —
call ends → create ticket → assign → chase in 48 h. That's genuinely its shape.

For the dialogue layer, what the industry converged on is slot-filling over a small
state machine (VoiceXML's lineage; Dialogflow CX, Lex, Rasa, Voiceflow all land
here). That's what this schema is. When you want the visual builder, render *this*
with React Flow or `diagram-js`.

## Not built yet

These configs describe behaviour nothing reads yet — the orchestrator that enforces
them doesn't exist. `validate.py` checks they are internally consistent; it cannot
check they produce good conversations.
