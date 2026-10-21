"""Check every tenant config: schema, cross-references, and the things that bite.

Run it after editing any tenant file.

    ../.venv/bin/python validate.py

It is deliberately strict about the parts the orchestrator will ENFORCE (slots,
patterns, tool references), and lenient about the parts the LLM interprets
(persona, outcome guidance). Those are different kinds of correctness.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
REQUIRED_TOP = ["tenant_id", "display_name", "slug", "locale", "voice", "persona"]
MAGPIE_VOICES = {"Aria", "Jason", "John", "Leo", "Sofia"}   # no cloning; fixed set
KNOWN_TOOLS = {"lookup_shipment", "reschedule_delivery", "check_line_status"}
SUPPORTED_LOCALES = {
    "en-US", "es-US", "fr-FR", "de-DE", "zh-CN", "vi-VN",
    "it-IT", "hi-IN", "ja-JP", "ko-KR", "ar-AR", "pt-BR",
}

problems: list[str] = []
warnings: list[str] = []


def err(t, m): problems.append(f"[{t}] {m}")
def warn(t, m): warnings.append(f"[{t}] {m}")


def check(path: Path) -> dict | None:
    try:
        cfg = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        err(path.name, f"YAML parse error: {str(e)[:120]}")
        return None

    tid = cfg.get("tenant_id", path.stem)

    for k in REQUIRED_TOP:
        if not cfg.get(k):
            err(tid, f"missing required key '{k}'")

    if cfg.get("locale") not in SUPPORTED_LOCALES:
        err(tid, f"locale {cfg.get('locale')!r} not supported by Magpie TTS")

    voice = (cfg.get("voice") or {}).get("speaker")
    if voice not in MAGPIE_VOICES:
        err(tid, f"voice.speaker {voice!r} is not a Magpie speaker {sorted(MAGPIE_VOICES)}")

    declared_tools = set(cfg.get("tools") or [])
    for t in declared_tools:
        if t not in KNOWN_TOOLS:
            err(tid, f"declares unknown tool {t!r}")

    # --- flows -----------------------------------------------------------
    flows = cfg.get("flows") or []
    names = [f.get("intent") for f in flows]
    if len(names) != len(set(names)):
        err(tid, "duplicate intent names")

    for f in flows:
        intent = f.get("intent", "?")
        if not f.get("internal") and not f.get("utterances"):
            warn(tid, f"flow {intent!r} has no example utterances — routing will be weak")

        # a flow that calls a tool must declare it
        tool = f.get("tool")
        if tool and tool not in declared_tools:
            err(tid, f"flow {intent!r} uses tool {tool!r} not in tools:")

        for ref in (f.get("requires_flow"), f.get("then_flow")):
            if ref and ref not in names:
                err(tid, f"flow {intent!r} references unknown flow {ref!r}")

        for slot in f.get("slots") or []:
            sn = slot.get("name", "?")
            pat = slot.get("pattern")
            if not pat:
                err(tid, f"slot {intent}.{sn} has no pattern — nothing to enforce")
            else:
                try:
                    re.compile(pat)
                except re.error as e:
                    err(tid, f"slot {intent}.{sn} pattern does not compile: {e}")
            for k in ("prompt", "reprompt", "max_attempts"):
                if not slot.get(k):
                    err(tid, f"slot {intent}.{sn} missing {k!r}")
            # A digits/alphanumeric slot read aloud needs a longer silence window
            # or we cut the caller off mid-code.
            ep = (slot.get("endpointing") or {}).get("silence_ms")
            if slot.get("type") in ("digits", "alphanumeric") and (ep or 0) < 1200:
                warn(tid, f"slot {intent}.{sn} is read aloud but silence_ms is "
                          f"{ep or 'default'} — callers pause mid-code, expect cut-offs")

        # every tool-calling flow should say what to do when the tool finds nothing
        outcomes = f.get("outcomes") or {}
        if tool and "not_found" not in outcomes and "too_late" not in outcomes:
            warn(tid, f"flow {intent!r} has no not_found outcome — undefined behaviour "
                      f"when the tool returns nothing")

    # --- knowledge -------------------------------------------------------
    kn = cfg.get("knowledge") or {}
    total_words = 0
    for src in kn.get("sources") or []:
        p = HERE / src
        if not p.exists():
            err(tid, f"knowledge source missing: {src}")
        else:
            total_words += len(p.read_text().split())
    if kn.get("strategy") == "in_prompt" and total_words > 30000:
        warn(tid, f"in_prompt knowledge is ~{total_words} words — consider retrieval")

    if not flows and not (kn.get("sources") or []):
        err(tid, "tenant has neither flows nor knowledge — it can do nothing")

    cfg["_words"] = total_words
    return cfg


def main() -> int:
    files = sorted(HERE.glob("*.yaml"))
    if not files:
        print("no tenant configs found")
        return 1

    configs = [c for c in (check(f) for f in files) if c]

    slugs = [c.get("slug") for c in configs]
    if len(slugs) != len(set(slugs)):
        err("global", "duplicate slugs — the widget URL would be ambiguous")
    ids = [c.get("tenant_id") for c in configs]
    if len(ids) != len(set(ids)):
        err("global", "duplicate tenant_ids")

    print(f"\n{'tenant':<16} {'slug':<12} {'locale':<7} {'voice':<7} "
          f"{'flows':>5} {'slots':>5} {'tools':>5} {'kb words':>9}")
    print("-" * 74)
    for c in configs:
        flows = c.get("flows") or []
        nslots = sum(len(f.get("slots") or []) for f in flows)
        print(f"{c.get('tenant_id',''):<16} {c.get('slug',''):<12} "
              f"{c.get('locale',''):<7} {(c.get('voice') or {}).get('speaker',''):<7} "
              f"{len(flows):>5} {nslots:>5} {len(c.get('tools') or []):>5} "
              f"{c.get('_words',0):>9}")

    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  ! {w}")
    if problems:
        print(f"\n{len(problems)} error(s):")
        for p in problems:
            print(f"  x {p}")
        return 1

    print(f"\nall {len(configs)} tenant(s) valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
