"""Call simulator: scripted callers driving the real orchestrator over gRPC.

    .venv/bin/python data/simulator/simulate.py --calls 200 --concurrency 8

Each simulated call is a real call to the orchestrator (flows, MCP tools, the
LLM, call events to Kafka), except that the caller is a script and the audio
side is skipped: lines arrive as final transcripts, the way the media edge
would send them. StartCall carries synthetic=true and channel="simulator", so
every metric downstream can tell load tests from callers (DDIA.md: provenance).

Point the orchestrator at data/simulator/fake_llm.py (--llm-url) unless you
want to spend Groq tokens; see data/simulator/README.md for the full recipe.

The scripts are what the live calls actually looked like: openers STT mangled
("may locate the parcel"), IDs read out character by character, codes that are
wrong or unknown, reschedules in several phrasings, callers who give up, and
callers who talk over the agent (barge-in).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import grpc

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services" / "orchestrator"), str(ROOT / "libs")]

from aicc_events import new_ulid
from gen import orchestrator_pb2 as pb
from gen import orchestrator_pb2_grpc as pb_grpc


def spoken(code: str) -> str:
    """One of the ways STT writes a code read aloud."""
    words = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
             "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
    return random.choice([
        code,
        f"it's {code}",
        " ".join(code),                                        # "A C 4 4 7 1 …"
        ", ".join(code) + ".",                                 # "A, C, 4, 4, …"
        " ".join(words.get(c, c.lower()) for c in code),       # "a c four four …"
    ])


SHIPMENTS = ["AC4471X209", "AC8823B110", "AC1199Z044", "AC0000A000"]
UNKNOWN_SHIPMENTS = ["AC9999Z999", "AC1234B567"]
LINES = ["0142887711", "0298445102", "0155093388", "0100000000"]

TRACK_OPENERS = ["where is my parcel", "track my delivery", "has my package shipped",
                 "may locate the parcel.", "I'm calling about a shipment"]
RESCHEDULE_ASKS = ["can you postpone it for 3 days", "just postpone it for three days",
                   "move it to the 5th of October", "reschedule it to October 3rd",
                   "I won't be home, can you deliver it another day", "next Monday please"]
GIVE_UP = ["um I don't know", "no idea sorry", "it's on the email somewhere", "hang on"]


# Scenario = (tenant slug, weight, builder returning the caller's lines).
def _track_ok():
    return [random.choice(TRACK_OPENERS), spoken(random.choice(SHIPMENTS)), "thanks, bye"]


def _track_then_reschedule():
    return [random.choice(TRACK_OPENERS), spoken("AC4471X209"),
            random.choice(RESCHEDULE_ASKS), "great, thank you"]


def _bare_id():
    return [f"is {random.choice(SHIPMENTS)}.", "ok thanks"]


def _unknown_id():
    return [random.choice(TRACK_OPENERS), spoken(random.choice(UNKNOWN_SHIPMENTS)), "ok bye"]


def _gives_up():
    return [random.choice(TRACK_OPENERS)] + random.sample(GIVE_UP, 3)


def _acme_open():
    return [random.choice(["what are your opening hours", "do you deliver on Sundays",
                           "how much does express cost"]), "ok thanks"]


def _line_check():
    return [random.choice(["my internet is down", "no connection", "the wifi isn't working"]),
            spoken(random.choice(LINES)), "thanks"]


def _swiftnet_faq():
    return [random.choice(["how do I restart my router", "what does the red light mean"]),
            "ok"]


def _maisonverte():
    return [random.choice(["Quels sont vos horaires d'ouverture ?",
                           "Avez-vous des plantes d'intérieur ?",
                           "Comment arroser un ficus ?"]), "Merci, au revoir."]


SCENARIOS = [
    ("acme", 25, _track_ok),
    ("acme", 15, _track_then_reschedule),
    ("acme", 8, _bare_id),
    ("acme", 8, _unknown_id),
    ("acme", 6, _gives_up),
    ("acme", 6, _acme_open),
    ("swiftnet", 18, _line_check),
    ("swiftnet", 6, _swiftnet_faq),
    ("maisonverte", 8, _maisonverte),
]


@dataclass
class Result:
    scenario: str
    tenant: str
    turns: int = 0
    hung_up_by_agent: bool = False
    error: str = ""
    seconds: float = 0.0
    said: list[str] = field(default_factory=list)


async def run_call(stub, scenario, tenant: str, lines: list[str], args) -> Result:
    res = Result(scenario=scenario, tenant=tenant)
    outbox: asyncio.Queue = asyncio.Queue()
    last_directive = time.monotonic()
    answered = asyncio.Event()
    hung_up = asyncio.Event()
    t0 = time.monotonic()

    async def requests():
        yield pb.CallEvent(start=pb.StartCall(tenant_slug=tenant, call_id=new_ulid(),
                                              synthetic=True, channel="simulator"))
        while (msg := await outbox.get()) is not None:
            yield msg

    async def read(stream):
        nonlocal last_directive
        async for d in stream:
            last_directive = time.monotonic()
            # Any directive means the agent took the turn. Waiting for speech
            # alone hangs after a barge-in that cut the reply before its first
            # clause; TurnInfo always arrives.
            answered.set()
            if d.HasField("say"):
                res.said.append(d.say.text)
            elif d.HasField("hangup"):
                res.hung_up_by_agent = True
                hung_up.set()
                return

    async def agent_finished(timeout: float):
        """Answered, then went quiet: the turn is over (there is no end-of-turn directive)."""
        deadline = time.monotonic() + timeout
        try:
            await asyncio.wait_for(answered.wait(), timeout)
        except TimeoutError:
            return
        while time.monotonic() < deadline and not hung_up.is_set():
            if time.monotonic() - last_directive > args.quiet_s:
                return
            await asyncio.sleep(0.05)

    stream = stub.Session(requests())
    reader = asyncio.create_task(read(stream))
    try:
        await agent_finished(args.turn_timeout_s)               # the greeting
        for line in lines:
            if hung_up.is_set():
                break
            await asyncio.sleep(random.uniform(*args.think_s))   # caller thinks
            answered.clear()
            await outbox.put(pb.CallEvent(transcript=pb.Transcript(text=line, is_final=True)))
            res.turns += 1
            # Now and then the caller talks over the reply: a barge-in.
            if random.random() < args.barge_in_rate:
                await asyncio.sleep(random.uniform(0.05, 0.4))
                await outbox.put(pb.CallEvent(speech_started=pb.SpeechStarted()))
            await agent_finished(args.turn_timeout_s)
    except grpc.aio.AioRpcError as exc:
        res.error = exc.code().name
    finally:
        if not hung_up.is_set():
            await outbox.put(pb.CallEvent(end=pb.EndCall(reason="hangup")))
        await outbox.put(None)
        try:
            await asyncio.wait_for(reader, 5)
        except (TimeoutError, grpc.aio.AioRpcError):
            reader.cancel()
        res.seconds = time.monotonic() - t0
    return res


async def main_async(args) -> int:
    weights = [w for _, w, _ in SCENARIOS]
    sem = asyncio.Semaphore(args.concurrency)
    results: list[Result] = []

    async with grpc.aio.insecure_channel(args.orchestrator) as channel:
        stub = pb_grpc.OrchestratorStub(channel)

        async def one(i: int):
            tenant, _, build = random.choices(SCENARIOS, weights)[0]
            async with sem:
                r = await run_call(stub, build.__name__.lstrip("_"), tenant, build(), args)
            results.append(r)
            if args.verbose or r.error:
                print(f"[{i:4d}] {r.tenant:<12} {r.scenario:<24} turns={r.turns} "
                      f"{'ESCALATED ' if r.hung_up_by_agent else ''}{r.error} {r.seconds:.1f}s")
            elif len(results) % 25 == 0:
                print(f"  {len(results)}/{args.calls} calls done")

        t0 = time.monotonic()
        await asyncio.gather(*(one(i) for i in range(args.calls)))

    by = Counter((r.tenant, r.scenario) for r in results)
    errors = sum(1 for r in results if r.error)
    print(f"\n{len(results)} calls in {time.monotonic() - t0:.0f}s, "
          f"{sum(r.turns for r in results)} caller turns, "
          f"{sum(r.hung_up_by_agent for r in results)} escalated, {errors} errors")
    for (tenant, scenario), n in sorted(by.items()):
        print(f"  {tenant:<12} {scenario:<24} {n}")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orchestrator", default="127.0.0.1:50052")
    ap.add_argument("--calls", type=int, default=50)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--seed", type=int, default=None, help="repeat the same mix of calls")
    ap.add_argument("--think-s", type=float, nargs=2, default=(0.1, 0.6),
                    metavar=("MIN", "MAX"), help="caller pause before each line")
    ap.add_argument("--quiet-s", type=float, default=0.6,
                    help="agent silence that counts as the end of its turn")
    ap.add_argument("--turn-timeout-s", type=float, default=15.0)
    ap.add_argument("--barge-in-rate", type=float, default=0.05)
    ap.add_argument("-v", "--verbose", action="store_true", help="one line per call")
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
