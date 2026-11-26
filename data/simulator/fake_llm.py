"""A stand-in LLM for the call simulator: OpenAI-compatible, streaming, free.

    .venv/bin/python data/simulator/fake_llm.py --port 8099
    # orchestrator: --llm-url http://127.0.0.1:8099/v1/chat/completions

Why not Groq: hundreds of simulated calls would hit its rate limits, cost tokens,
and make every run different. This server answers instantly in shape but not in
time: time-to-first-token and token pace are drawn from distributions shaped
like the ones measured live (ttft ~300 ms median, a long tail), so the latency
columns downstream look like real data and the quality checks have something
to bite on.

Replies are canned but grounded: a turn that carries tool data gets a sentence
built from that data (the shipment ID included, as a real agent would echo it),
which is exactly the kind of identifier-in-free-text the silver layer must mask.
An open turn answers with the reference sentence closest to the question (the
tenant's FAQ, or the passages a document search returned), and says "the
documents don't cover that" when the search found nothing: what a well-behaved
model does, so transcripts read like real calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time

from aiohttp import web

OPEN_REPLIES = [
    "I can help with that. Could you tell me a little more?",
    "Sure. I can track a parcel or move a delivery for you.",
    "Of course. What would you like to do today?",
    "Happy to help. Our team is available from nine to six.",
]
GENERIC_REPLIES = [
    "I can help with that. Could you tell me a little more?",
    "Sure. What would you like to do?",
    "Of course. Can you describe the problem for me?",
]
FR_REPLIES = [
    "Bien sûr. Nos horaires sont de neuf heures à dix-neuf heures.",
    "Avec plaisir. Nous avons un large choix de plantes d'intérieur.",
]


def _reply(messages: list[dict]) -> str:
    system = messages[0]["content"] if messages else ""
    data = re.search(r"tool returned this data:\n(\{.*\})", system)
    if data:
        try:
            d = json.loads(data.group(1))
        except json.JSONDecodeError:
            d = {}
        sid = d.get("shipment_id", "")
        match d.get("outcome"):
            case "in_transit" if d.get("location") == "out for delivery":
                return f"Your parcel {sid} is out for delivery and should arrive today."
            case "in_transit":
                return (f"Your parcel {sid} is at the {d.get('location', 'depot')}. "
                        f"It should arrive on {d.get('estimated_delivery', 'soon')}.")
            case "delivered":
                return f"Parcel {sid} was delivered and signed for by {d.get('signed_by', 'someone')}."
            case "delayed":
                new = d.get("new_estimate") or d.get("estimated_delivery") or "soon"
                return f"I'm sorry, parcel {sid} is delayed. The new estimate is {new}."
            case "ok":
                return f"Done. Parcel {sid} will now arrive on {d.get('new_date')}."
            case "too_late":
                return "I'm afraid it can no longer be changed."
            case "not_found":
                return "I can't find that one. Could you check the ID?"
            case "line_ok":
                return f"The line {d.get('phone_number')} looks fine from here. Try restarting the router."
            case "area_outage":
                return f"There is an outage affecting {d.get('phone_number')}. Engineers are on it."
            case "account_suspended":
                return "That account is suspended. I'll pass you to our accounts team."
            case outcome:
                return f"The check came back as {outcome}."
    if re.search(r"\b(Vous|vous|Répondez)\b", system):     # a French persona
        return random.choice(FR_REPLIES)
    user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    return _open_reply(system, user)


# -- open turns: answer from what the prompt gives, like a well-behaved model -------

_STOP = set("""a an the is are was were be been do does did i you we they it its it's my your our
of to in on at for from by with and or but if so as that this these those what which who whom
how when where why can could should would will shall may might must have has had not no yes
me us them there here about into than then just please hi hello ok okay thanks thank sorry
get got any some more most very really also""".split())
_HUMAN = re.compile(r"\b(human|person|someone|agent|engineer|representative|operator)\b", re.I)
_CLOSING = re.compile(r"\b(thanks|thank you|cheers|bye|goodbye|brilliant|perfect)\b", re.I)


_IRREGULAR = {"kept": "keep", "paid": "pay", "held": "hold", "done": "do"}


def _words(text: str) -> set[str]:
    """Content words, crudely stemmed: 'records' and 'record', 'kept' and 'keep' meet."""
    return {_IRREGULAR.get(w, w).rstrip("s")
            for w in re.findall(r"[a-z0-9]+(?:-[0-9]+)?", text.lower())
            if w not in _STOP and len(w) > 1}


def _best_sentence(reference: str, question: str, min_shared: int = 1) -> str | None:
    """The reference sentence sharing most content words with the question, if it
    shares at least min_shared (fewer when the question has fewer)."""
    q = _words(question)
    best, best_score = None, (min(min_shared, len(q)) - 1, 0.0)
    for raw in re.split(r"(?<=[.!?])\s+|\n+", reference):
        if raw.lstrip().startswith("#"):        # a heading names a topic, it answers nothing
            continue
        s = re.sub(r"^[\s*\-\[\]0-9]*", "", raw).replace("**", "").strip()
        if len(s) < 12 or s.startswith("---"):
            continue
        words = _words(s)
        n = len(q & words)
        score = (n, n / max(1, len(words)))     # ties: the more focused sentence
        if n and score > best_score:
            best, best_score = s, score
    return best


def _open_reply(system: str, user: str) -> str:
    """An answer grounded in the prompt's reference material, or an honest 'not covered'."""
    company = re.search(r"phone assistant for ([^.,\n]+)", system)
    name = company.group(1).strip() if company else "the company"
    if _CLOSING.search(user) and "?" not in user:
        return random.choice(["You're welcome. Goodbye!", "Glad I could help. Bye for now.",
                              "You're welcome, have a good day."])
    if re.search(r"documents is unavailable right now", system):
        return f"I'm sorry, I can't check that at the moment. Please contact {name} directly."
    if re.search(r"documents have nothing on what the caller just said", system):
        return (f"I'm sorry, {name}'s documents don't cover that. "
                f"Please contact {name} directly about it.")
    found = re.search(r"Reference material from .*? documents.*?\n\n(.*)", system, re.S)
    if found:
        # A search always returns its nearest passages, related or not: one shared
        # word ("order" in "order new safety boots") is not an answer.
        best = _best_sentence(found.group(1).split("\n\nIMPORTANT")[0], user, min_shared=2)
        if best:
            if not best[1:2].isupper():         # "SCP-101 ..." keeps its capitals
                best = best[0].lower() + best[1:]
            return f"According to our documents, {best}"
        return f"I'm sorry, our documents don't cover that. Please contact {name} directly."
    if _HUMAN.search(user):
        return ("I understand you'd like to speak to someone. I can help with most things "
                "right here. What do you need?")
    inline = re.search(r"Reference material\. Answer only from this[^\n]*\n(.*)", system, re.S)
    if inline:
        best = _best_sentence(inline.group(1).split("\n\nIMPORTANT")[0], user)
        if best:
            return best if best.endswith(".") else best + "."
    if "Acme" in name:
        return random.choice(OPEN_REPLIES)
    return random.choice(GENERIC_REPLIES)


def _ttft_s(args) -> float:
    # Lognormal: most first tokens near the median, a few very slow ones.
    return min(args.ttft_ms_median / 1000 * random.lognormvariate(0, 0.45), 8.0)


async def completions(request: web.Request) -> web.StreamResponse:
    args = request.app["args"]
    body = await request.json()
    if random.random() < args.error_rate:
        return web.json_response({"error": {"message": "simulated overload"}}, status=503)

    text = _reply(body.get("messages") or [])
    tokens = re.findall(r"\S+\s*", text)
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    await asyncio.sleep(_ttft_s(args))
    created = int(time.time())
    try:
        for tok in tokens:
            chunk = {"id": "sim", "object": "chat.completion.chunk", "created": created,
                     "model": body.get("model", "sim"),
                     "choices": [{"index": 0, "delta": {"content": tok}}]}
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
            await asyncio.sleep(random.uniform(0.005, 0.02))
        prompt_tokens = sum(len(m.get("content", "")) for m in body.get("messages") or []) // 4
        final = {"id": "sim", "object": "chat.completion.chunk", "created": created,
                 "choices": [], "usage": {"prompt_tokens": prompt_tokens,
                                          "completion_tokens": len(tokens)}}
        await resp.write(f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n".encode())
        await resp.write_eof()
    except ConnectionResetError:
        pass        # the client stopped reading: a barge-in cut the reply short
    return resp


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--ttft-ms-median", type=float, default=300)
    ap.add_argument("--error-rate", type=float, default=0.0,
                    help="fraction of requests answered 503, to exercise error events")
    args = ap.parse_args()
    app = web.Application()
    app["args"] = args
    app.router.add_post("/v1/chat/completions", completions)
    web.run_app(app, host=args.host, port=args.port, access_log=None, print=None)


if __name__ == "__main__":
    main()
