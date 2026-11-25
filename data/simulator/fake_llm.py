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
            case "in_transit":
                return (f"Your parcel {sid} is at the {d.get('location', 'depot')}. "
                        f"It should arrive on {d.get('estimated_delivery', 'soon')}.")
            case "delivered":
                return f"Parcel {sid} was delivered and signed for by {d.get('signed_by', 'someone')}."
            case "delayed":
                return f"I'm sorry, parcel {sid} is delayed. The new estimate is {d.get('estimated_delivery', 'soon')}."
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
    return random.choice(OPEN_REPLIES)


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
