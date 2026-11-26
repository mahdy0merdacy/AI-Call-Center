"""Backfill ~30 days of call history: scripted callers through the REAL orchestrator.

    # what would be generated (no network, nothing published)
    .venv/bin/python data/simulator/history.py --dry-run

    # everything in-process and offline: fixture tools, a stand-in document search,
    # events counted but not published. Proves the wiring end to end.
    .venv/bin/python data/simulator/history.py --offline --limit 50

    # the real thing (in the cluster: deploy/k3s/tools/populate-history.yaml)
    KAFKA_BOOTSTRAP=... SCHEMA_REGISTRY_URL=... MCP_SERVERS=... KNOWLEDGE_URL=... \
    PLATFORM_DATABASE_URL=... python data/simulator/history.py --days 30 --concurrency 8

Why replay instead of writing events: the lake's quality gates check every event
against the schema AND against what the orchestrator really does (seq order,
turn shapes, masking). So nothing here builds an event. Each call is a real
OrchestratorServicer.Session, driven in-process the way the media edge drives it
over gRPC: the real flow engine, the real MCP tools, the real knowledge search for
the platform tenant, and the real EventPublisher on Kafka. Only the LLM is the
fake one (data/simulator/fake_llm.py, served in-process), and only the caller is
a script (history_scenarios.py).

Backdating, without touching the orchestrator or libs:

  - Every call gets a virtual start time in the last N days, shaped like real
    traffic (weekday and hour patterns per tenant, a mild trend, an outage day).
  - BackdatedPublisher (an EventPublisher subclass) restamps occurred_at_ms on
    each event of a tracked call from that call's CallClock: virtual start + the
    real time elapsed since + the time a phone call would have spent talking
    (caller speech, think time, the agent's spoken replies), which in-process
    takes no time at all. CallEnded.duration_ms is set from the same clock.
  - The agent's "today" (CallFlowState.today: "postpone it 3 days", "next
    Monday") is pinned to the call's virtual date as soon as the session exists.
  - call_id is a ULID carrying the virtual start time.

Every call is StartCall(synthetic=true, channel="history").

What is NOT backdated: the Kafka record timestamp (so topic retention keeps the
records) and event_id (a fresh ULID; consumers only dedupe on it). The bronze
ingest-lag check therefore sees these events as days late, as it should.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
import time
import warnings
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

# abspath, not resolve(): a ConfigMap mounts each file as a symlink into a
# timestamped subfolder, and the layout around it is what counts.
HERE = Path(os.path.abspath(__file__)).parent
# <repo> from a checkout; /srv/aicc in the orchestrator image (AICC_ROOT overrides).
ROOT = Path(os.environ.get("AICC_ROOT") or HERE.parents[1])
for p in (HERE, ROOT / "libs", ROOT / "services" / "orchestrator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import grpc
import history_scenarios as sc
from aicc_events import EVENTS_TOPIC, PARTIALS_TOPIC, EventPublisher, ev, new_ulid
from app.flow import TENANTS_DIR, load_tenants
from app.knowledge import KnowledgeClient, Passage, SearchResult
from app.server import OrchestratorServicer
from app.tenants import (
    DirectoryUnavailable,
    PlatformTenant,
    PostgresPlatformSource,
    TenantDirectory,
    platform_tenant,
)
from app.tools import ToolRegistry, parse_mcp_servers

from gen import orchestrator_pb2 as pb

log = logging.getLogger("history")

CHANNEL = "history"
TENANT_KEYS = ("acme", "swiftnet", "stockline")
SAMPLE_RATE = 16_000            # at_sample on transcripts: 16 kHz, like the edge


# -- time --------------------------------------------------------------------------


def resolve_tz(name: str) -> tzinfo:
    """The tenants' local zone. Falls back to a fixed offset where there is no tz
    database (a slim image without tzdata): 'Europe/Paris' -> UTC+1."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        fixed = {"Europe/Paris": 1, "Europe/London": 0, "UTC": 0}.get(name, 0)
        log.warning("no tz database for %s: using a fixed UTC%+d", name, fixed)
        return timezone(timedelta(hours=fixed))


def local_epoch_ms(day: date, second_of_day: int, tz: tzinfo) -> int:
    """Epoch ms of a local wall-clock time on `day`."""
    h, rem = divmod(second_of_day, 3600)
    m, s = divmod(rem, 60)
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=tz).timestamp() * 1000)


def local_date(epoch_ms: int, tz: tzinfo) -> date:
    return datetime.fromtimestamp(epoch_ms / 1000, tz).date()


class CallClock:
    """One call's virtual time: start + real time elapsed + time spent talking.

    In-process a call takes a second or two; on the phone the same call takes a
    minute or more, because people talk and listen. advance() adds that time.
    """

    def __init__(self, start_ms: int, monotonic: Callable[[], float] = time.monotonic):
        self.start_ms = start_ms
        self._mono = monotonic
        self._anchor_real = monotonic()
        self._anchor_virtual = float(start_ms)

    def now_ms(self) -> int:
        return int(self._anchor_virtual + (self._mono() - self._anchor_real) * 1000)

    def advance(self, seconds: float) -> None:
        self._anchor_virtual += max(0.0, seconds) * 1000

    def elapsed_ms(self) -> int:
        return self.now_ms() - self.start_ms


def agent_speech_s(words: int) -> float:
    """How long TTS takes to say `words` (about 160 words a minute)."""
    return words / 2.7


def caller_turn_s(line: str, rng: random.Random) -> float:
    """Think, then say the line. Codes are read slowly, with pauses."""
    speak = len(line.split()) / 2.6
    if sum(c.isdigit() for c in line) >= 4 or len([w for w in line.split() if len(w) == 1]) >= 4:
        speak *= 1.8
    return rng.uniform(0.6, 2.2) + speak


# -- the plan ------------------------------------------------------------------------


@dataclass
class CallPlan:
    index: int
    tenant: str                 # acme | swiftnet | stockline (the scenario key)
    start_ms: int               # virtual start, epoch ms
    day: date                   # its local date: the agent's "today"
    script: sc.Script
    barge_in: list[bool]        # per caller line: talk over the reply
    seed: int                   # for this call's think times


@dataclass
class PlanConfig:
    tenants: tuple[str, ...] = TENANT_KEYS
    days: int = 30
    per_day: dict[str, float] = field(default_factory=lambda: dict(sc.DEFAULT_CALLS_PER_DAY))
    seed: int = 1126
    tz: tzinfo = UTC
    now: datetime | None = None             # default: the real now
    barge_in_rate: float = 0.03
    outage_day: bool = True                 # one day of swiftnet outage traffic
    min_age_s: int = 120                    # nothing closer to now than this


def build_plan(cfg: PlanConfig, fixtures: sc.Fixtures) -> list[CallPlan]:
    """Every call to generate, in virtual-time order. Deterministic for a seed."""
    rng = random.Random(cfg.seed)
    now = cfg.now or datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    latest_ms = now_ms - cfg.min_age_s * 1000
    first = window_start(cfg)
    outage = outage_day(cfg)

    plans: list[CallPlan] = []
    for i in range(cfg.days):
        day = first + timedelta(days=i)
        trend = 0.9 + 0.2 * i / max(1, cfg.days - 1)        # +20% over the window
        for tenant in cfg.tenants:
            mean, boost = cfg.per_day[tenant], None
            if tenant == "swiftnet" and day == outage:
                mean, boost = mean * 1.7, {"swift_outage": 5.0}
            n = sc.calls_on_day(rng, tenant, day.weekday(), mean, trend)
            weights = sc.hour_weights(tenant, day.weekday())
            for _ in range(n):
                hour = rng.choices(range(24), weights)[0]
                start = local_epoch_ms(day, hour * 3600 + rng.randrange(3600), cfg.tz)
                call_rng = random.Random(rng.getrandbits(64))
                script = sc.pick_script(tenant, call_rng, fixtures, boost)
                barge = [call_rng.random() < cfg.barge_in_rate for _ in script.lines]
                if start > latest_ms:
                    continue        # later today: has not happened yet
                plans.append(CallPlan(0, tenant, start, day, script, barge,
                                      call_rng.getrandbits(32)))
    plans.sort(key=lambda p: p.start_ms)
    for i, p in enumerate(plans):
        p.index = i
    return plans


def window_start(cfg: PlanConfig) -> date:
    """The first local day of the window; the last is today."""
    now = cfg.now or datetime.now(UTC)
    return now.astimezone(cfg.tz).date() - timedelta(days=cfg.days - 1)


def outage_day(cfg: PlanConfig) -> date | None:
    """The day SwiftNet has an area outage: a weekday in the older two-thirds of the
    window, when swiftnet is in the run. Its own random stream, so it never shifts
    the rest of the plan."""
    if not (cfg.outage_day and "swiftnet" in cfg.tenants and cfg.days >= 7):
        return None
    rng = random.Random(f"{cfg.seed}:outage")
    day = window_start(cfg) + timedelta(days=rng.randrange(cfg.days * 2 // 3))
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


# -- publishing, backdated -----------------------------------------------------------


class BackdatedPublisher(EventPublisher):
    """The real publisher, with each tracked call's events stamped by its CallClock.

    Also tallies what went out per tenant, for the summary.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._calls: dict[str, tuple[str, CallClock]] = {}
        self.tally: dict[str, Counter[str]] = defaultdict(Counter)
        self.tools: dict[str, Counter[str]] = defaultdict(Counter)
        self.gaps: dict[str, Counter[str]] = defaultdict(Counter)

    def track(self, call_id: str, tenant: str, clock: CallClock) -> None:
        self._calls[call_id] = (tenant, clock)

    def untrack(self, call_id: str) -> None:
        self._calls.pop(call_id, None)

    def publish(self, event: ev.Event) -> None:
        entry = self._calls.get(event.call_id)
        if entry is not None:
            tenant, clock = entry
            event.occurred_at_ms = clock.now_ms()
            if event.WhichOneof("payload") == "call_ended":
                event.call_ended.duration_ms = max(0, clock.elapsed_ms())
            self._tally(tenant, event)
        super().publish(event)

    def _tally(self, tenant: str, event: ev.Event) -> None:
        kind = event.WhichOneof("payload") or "?"
        t = self.tally[tenant]
        t["events"] += 1
        t[kind] += 1
        if kind == "tool_invoked":
            self.tools[tenant][f"{event.tool_invoked.tool}:{event.tool_invoked.outcome}"] += 1
        elif kind == "knowledge_searched":
            k = event.knowledge_searched
            t["search_failed" if k.failed else "search_covered" if k.covered
              else "search_uncovered"] += 1
            if not k.covered and not k.failed:
                self.gaps[tenant][k.query] += 1


async def wait_for_schemas(pub: EventPublisher, timeout_s: float) -> bool:
    """Until the registry has answered: before that, every event is dropped as no_schema."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if EVENTS_TOPIC in getattr(pub, "_schema_ids", {}):
            return True
        await asyncio.sleep(0.25)
    return False


class MemoryProducer:
    """A Kafka producer stand-in: keeps (or only counts) what would be sent."""

    def __init__(self, keep: bool = True):
        self.keep = keep
        self.records: list[dict[str, Any]] = []
        self.count = 0

    def produce(self, topic, key, value, on_delivery=None):
        self.count += 1
        if self.keep:
            self.records.append({"topic": topic, "key": key, "value": value})
        if on_delivery is not None:
            on_delivery(None, None)

    def poll(self, timeout):
        return 0

    def flush(self, timeout):
        return 0

    def __len__(self) -> int:
        return 0

    def events(self) -> list[ev.Event]:
        """Decoded: skip the 6-byte Schema Registry header."""
        return [ev.Event.FromString(r["value"][6:]) for r in self.records]


def memory_publisher(keep: bool = True) -> tuple[BackdatedPublisher, MemoryProducer]:
    producer = MemoryProducer(keep)
    pub = BackdatedPublisher(ev.SERVICE_ORCHESTRATOR, producer,
                             schema_ids={EVENTS_TOPIC: 1, PARTIALS_TOPIC: 1},
                             instance="history-offline", build="history")
    return pub, producer


# -- offline stand-ins: the tools and the document search, with no network -----------


def fixture_tool_result(tool: str, args: dict, fx: sc.Fixtures) -> dict[str, Any]:
    """What services/mcp-servers answer, from the same fixtures (offline runs only)."""
    if tool == "lookup_shipment":
        sid = str(args.get("shipment_id", "")).strip().upper()
        rec = fx.shipments.get(sid)
        if rec is None:
            return {"outcome": "not_found", "shipment_id": sid}
        return {**{k: v for k, v in rec.items() if not k.startswith("_")}, "shipment_id": sid}
    if tool == "reschedule_delivery":
        sid = str(args.get("shipment_id", "")).strip().upper()
        rec = fx.shipments.get(sid)
        if rec is None:
            return {"outcome": "not_found", "shipment_id": sid}
        if rec.get("outcome") == "delivered":
            return {"outcome": "too_late", "reason": "already_delivered", "shipment_id": sid}
        if rec.get("reschedulable") is False or rec.get("location") == "out for delivery":
            return {"outcome": "too_late", "reason": "out_for_delivery", "shipment_id": sid}
        return {"outcome": "ok", "shipment_id": sid, "new_date": args.get("new_date"),
                "previous_estimate": rec.get("estimated_delivery")}
    if tool == "check_line_status":
        num = "".join(c for c in str(args.get("phone_number", "")) if c.isdigit())
        rec = fx.lines.get(num)
        if rec is None:
            return {"outcome": "not_found", "phone_number": num}
        return {**{k: v for k, v in rec.items() if not k.startswith("_")}, "phone_number": num}
    return {"outcome": "error", "reason": "unknown_tool"}


def install_fixture_tools(fx: sc.Fixtures) -> Callable[[], None]:
    """Answer MCP calls from the fixtures instead of the network. Returns the undo.

    Replaces only the transport (ToolRegistry._invoke): the allowlist, the timeout
    and the latency measurement around it are the real code.
    """
    original = ToolRegistry.__dict__["_invoke"]

    async def _invoke(url: str, tool: str, args: dict) -> dict:
        await asyncio.sleep(random.uniform(0.015, 0.06))
        return fixture_tool_result(tool, args, fx)

    ToolRegistry._invoke = staticmethod(_invoke)     # type: ignore[method-assign]

    def undo() -> None:
        ToolRegistry._invoke = original              # type: ignore[method-assign]
    return undo


OFFLINE_MCP = {"lookup_shipment": "http://offline.invalid/mcp",
               "reschedule_delivery": "http://offline.invalid/mcp",
               "check_line_status": "http://offline.invalid/mcp"}

_STOP = set("""a an the is are was were be been do does did i you we they it my your our of to in
on at for from by with and or but if so as that this what which who how when where why can could
should would will have has had not no me us there about into than then just please hi ok okay
thanks sorry any some more very really also i'm i've don't haven't isn't""".split())


_IRREGULAR = {"kept": "keep", "paid": "pay", "held": "hold"}


def _terms(text: str) -> set[str]:
    words = "".join(c.lower() if c.isalnum() or c == "-" else " " for c in text).split()
    return {_IRREGULAR.get(w, w).rstrip("s") for w in words if w not in _STOP and len(w) > 1}


class OfflineKnowledge(KnowledgeClient):
    """Searches Stock Control Policy sections by word overlap, scored like bge-m3:
    on-topic ~0.55-0.75, off-topic ~0.30-0.45 (see app/knowledge.py)."""

    def __init__(self, sections: list[tuple[str, str]] = sc.STOCK_CONTROL_POLICY,
                 title: str = sc.STOCK_CONTROL_POLICY_TITLE):
        super().__init__("http://offline.invalid")
        self.sections = sections
        self.title = title

    def score(self, query: str) -> list[tuple[float, str, str]]:
        q = _terms(query)
        out = []
        for section, text in self.sections:
            n = len(q & _terms(section + " " + text))
            out.append((min(0.78, 0.31 + 0.12 * n), section, text))
        return sorted(out, key=lambda x: -x[0])

    async def search(self, session, tenant_id: str, query: str) -> SearchResult:
        await asyncio.sleep(random.uniform(0.02, 0.08))
        ranked = self.score(query)[: self.k]
        passages = [Passage(document_id="offline-policy", title=self.title, section=section,
                            content=text, score=s) for s, section, text in ranked]
        return SearchResult(passages, took_ms=30, best_similarity=ranked[0][0] if ranked else None)


class BoundedKnowledge(KnowledgeClient):
    """The real client, with at most `limit` searches in flight: the embedding
    model is on CPU, and a backfill must not starve live calls."""

    def __init__(self, base_url: str, *, timeout_s: float, limit: int):
        super().__init__(base_url, timeout_s=timeout_s)
        self._sem = asyncio.Semaphore(limit)

    async def search(self, session, tenant_id: str, query: str) -> SearchResult:
        async with self._sem:
            return await super().search(session, tenant_id, query)


async def start_fake_llm(ttft_ms_median: float, error_rate: float = 0.0):
    """fake_llm.py's handler on 127.0.0.1, any free port. (runner, chat completions URL)."""
    import fake_llm
    from aiohttp import web

    app = web.Application()
    with warnings.catch_warnings():         # fake_llm reads app["args"]: a str key, on purpose
        warnings.simplefilter("ignore")
        app["args"] = argparse.Namespace(ttft_ms_median=ttft_ms_median, error_rate=error_rate)
    app.router.add_post("/v1/chat/completions", fake_llm.completions)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, f"http://127.0.0.1:{port}/v1/chat/completions"


# -- driving one call -----------------------------------------------------------------


class _Context:
    """The bit of a gRPC servicer context Session uses: abort() on a refused call."""

    async def abort(self, code: grpc.StatusCode, details: str = ""):
        raise grpc.aio.AbortError(f"{code.name}: {details}")


@dataclass
class CallOutcome:
    plan: CallPlan
    call_id: str = ""
    ok: bool = False
    hung_up_by_agent: bool = False
    turns: int = 0
    error: str = ""


async def _turn_done(servicer: OrchestratorServicer, call_id: str, prev: Any,
                     timeout_s: float) -> None:
    """Until the reply to the line just sent has finished (or the session is gone)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        s = servicer.active.get(call_id)
        if s is None:
            return
        if s.turn is not None and s.turn is not prev and s.turn.done():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"no reply within {timeout_s:.0f} s")


async def drive_call(servicer: OrchestratorServicer, publisher: BackdatedPublisher,
                     plan: CallPlan, slug: str, *, turn_timeout_s: float = 30.0) -> CallOutcome:
    """One backdated call through servicer.Session, as the media edge would drive it."""
    rng = random.Random(plan.seed)
    call_id = new_ulid(plan.start_ms)
    out = CallOutcome(plan, call_id)
    clock = CallClock(plan.start_ms)
    publisher.track(call_id, plan.tenant, clock)
    outbox: asyncio.Queue = asyncio.Queue()
    ready, spoke, hung_up = asyncio.Event(), asyncio.Event(), asyncio.Event()
    words = [0]                 # agent words since the caller last spoke

    async def requests():
        yield pb.CallEvent(start=pb.StartCall(tenant_slug=slug, call_id=call_id,
                                              synthetic=True, channel=CHANNEL))
        while (msg := await outbox.get()) is not None:
            yield msg

    async def read():
        async for d in servicer.Session(requests(), _Context()):
            kind = d.WhichOneof("directive")
            if kind == "ready":
                session = servicer.active.get(call_id)
                if session is not None:
                    session.agent.state.today = plan.day     # dates count from the call's day
                ready.set()
            elif kind == "say":
                words[0] += len(d.say.text.split())
                spoke.set()
            elif kind == "hangup":
                hung_up.set()

    reader = asyncio.create_task(read())
    try:
        waiter = asyncio.create_task(ready.wait())
        await asyncio.wait({waiter, reader}, timeout=15, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if not ready.is_set():
            if reader.done() and reader.exception() is not None:
                raise reader.exception()      # type: ignore[misc]
            raise RuntimeError("the call was not accepted")
        try:                                  # the greeting
            await asyncio.wait_for(spoke.wait(), 2.0)
        except TimeoutError:
            pass
        for i, line in enumerate(plan.script.lines):
            if hung_up.is_set():
                break
            clock.advance(agent_speech_s(words[0]) + caller_turn_s(line, rng))
            words[0] = 0
            session = servicer.active.get(call_id)
            if session is None:
                break
            prev = session.turn
            await outbox.put(pb.CallEvent(transcript=pb.Transcript(
                text=line, is_final=True, at_sample=clock.elapsed_ms() * SAMPLE_RATE // 1000)))
            out.turns += 1
            if plan.barge_in[i]:
                await asyncio.sleep(rng.uniform(0.05, 0.3))
                await outbox.put(pb.CallEvent(speech_started=pb.SpeechStarted(
                    at_sample=clock.elapsed_ms() * SAMPLE_RATE // 1000)))
            await _turn_done(servicer, call_id, prev, turn_timeout_s)
        # The last reply is spoken before anyone hangs up.
        clock.advance(agent_speech_s(words[0]) + rng.uniform(0.3, 1.5))
        if not hung_up.is_set():
            await outbox.put(pb.CallEvent(end=pb.EndCall(reason="hangup")))
        out.ok = True
    except Exception as exc:
        out.error = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        await outbox.put(None)
        try:
            await asyncio.wait_for(reader, 15)
        except TimeoutError:
            reader.cancel()
            out.ok, out.error = False, out.error or "session did not close"
        except Exception as exc:
            out.ok, out.error = False, out.error or f"{type(exc).__name__}: {exc}"[:200]
        publisher.untrack(call_id)
        out.hung_up_by_agent = hung_up.is_set()
    return out


async def _backpressure(publisher: EventPublisher, high: int = 10_000) -> None:
    """Keep librdkafka's queue (bounded at 20 000, then events are dropped) well below full."""
    producer = getattr(publisher, "_producer", None)
    try:
        # librdkafka has no "queue drained" signal to wait on: poll it.
        while producer is not None and len(producer) > high:  # noqa: ASYNC110
            await asyncio.sleep(0.2)
    except TypeError:
        return


async def run_calls(servicer: OrchestratorServicer, publisher: BackdatedPublisher,
                    plans: list[CallPlan], slugs: dict[str, str], *, concurrency: int = 8,
                    turn_timeout_s: float = 30.0,
                    on_done: Callable[[CallOutcome], None] | None = None) -> list[CallOutcome]:
    """All the calls, at most `concurrency` at a time, started in virtual-time order."""
    sem = asyncio.Semaphore(concurrency)
    results: list[CallOutcome] = []

    async def one(plan: CallPlan) -> None:
        async with sem:
            await _backpressure(publisher)
            res = await drive_call(servicer, publisher, plan, slugs[plan.tenant],
                                   turn_timeout_s=turn_timeout_s)
        results.append(res)
        if on_done is not None:
            on_done(res)

    await asyncio.gather(*(one(p) for p in plans))
    return results


# -- tenants ----------------------------------------------------------------------------


async def build_directory(keys: Iterable[str], slugs: dict[str, str], *, tenants_dir: Path,
                          stockline_id: str | None, stockline_name: str,
                          dsn: str | None) -> TenantDirectory:
    """The orchestrator's own tenant directory, holding just the tenants asked for."""
    keys = list(keys)
    yaml_tenants = load_tenants(tenants_dir)
    test = {slugs[k]: yaml_tenants[slugs[k]] for k in keys
            if k != "stockline" and slugs[k] in yaml_tenants}
    platform = None
    if "stockline" in keys:
        if stockline_id:
            test[slugs["stockline"]] = platform_tenant(PlatformTenant(
                id=stockline_id, name=stockline_name, slug=slugs["stockline"], is_active=True))
        elif dsn:
            platform = PostgresPlatformSource(dsn)
            await platform.open()
        else:
            raise SystemExit("stockline needs PLATFORM_DATABASE_URL (to resolve its tenant "
                             "UUID like the orchestrator does) or --stockline-id")
    directory = TenantDirectory(test, platform)
    for k in keys:
        tenant = None
        for _ in range(10):     # the pool opens in the background
            try:
                tenant = await directory.get(slugs[k])
                break
            except DirectoryUnavailable as exc:
                log.warning("%s", exc)
                await asyncio.sleep(1.5)
        if tenant is None:
            raise SystemExit(f"tenant {slugs[k]!r} ({k}) not found")
        if k == "stockline" and not tenant.knowledge_id:
            raise SystemExit(f"{slugs[k]!r} is not a platform tenant (no documents to search)")
        print(f"  tenant {k:<10} slug={tenant.slug:<14} tenant_id={tenant.tenant_id} "
              f"({tenant.source})")
    return directory


# -- reporting ----------------------------------------------------------------------------


def print_plan(plans: list[CallPlan], cfg: PlanConfig, samples: int = 3) -> None:
    print(f"\nplan: {len(plans)} calls over {cfg.days} days, seed {cfg.seed}")
    if not plans:
        return
    tenants = [t for t in cfg.tenants]
    by_day: dict[date, Counter[str]] = defaultdict(Counter)
    for p in plans:
        by_day[p.day][p.tenant] += 1
    print("\n  day         " + "".join(f"{t:>11}" for t in tenants))
    for d in sorted(by_day):
        print(f"  {d} {d.strftime('%a')}" + "".join(f"{by_day[d][t]:>11}" for t in tenants))
    outage = outage_day(cfg)
    if outage:
        print(f"  swiftnet outage day: {outage} ({by_day[outage]['swiftnet']} calls)")

    for t in tenants:
        mine = [p for p in plans if p.tenant == t]
        if not mine:
            continue
        print(f"\n  {t}: {len(mine)} calls, {sum(len(p.script.lines) for p in mine)} caller lines")
        for name, n in Counter(p.script.scenario for p in mine).most_common():
            print(f"    {name:<28} {n:>5}  {100 * n / len(mine):5.1f}%")
        hours = Counter(datetime.fromtimestamp(p.start_ms / 1000, cfg.tz).hour for p in mine
                        if p.day.weekday() < 5)
        peak = max(hours.values() or [1])
        print("    weekday hours: " + " ".join(f"{h:02d}:{'#' * round(8 * hours[h] / peak):<8}"
                                               for h in range(6, 23) if hours[h]))
        if t == "stockline":
            lines = [line for p in mine for line in p.script.lines]
            uncovered = {q for q, _ in sc.STOCKLINE_UNCOVERED}
            gaps = Counter(next((q for q in uncovered if line.endswith(q[1:])), None)
                           for line in lines)
            gaps.pop(None, None)
            share = sum(gaps.values()) / max(1, len(lines))
            print(f"    uncovered questions: {sum(gaps.values())}/{len(lines)} lines "
                  f"({100 * share:.0f}%); top: "
                  + "; ".join(f"{q} x{n}" for q, n in gaps.most_common(4)))
        for p in mine[:: max(1, len(mine) // samples)][:samples]:
            when = datetime.fromtimestamp(p.start_ms / 1000, cfg.tz).strftime("%Y-%m-%d %H:%M")
            print(f"    e.g. {when} {p.script.scenario}: " + " | ".join(p.script.lines))


def print_summary(results: list[CallOutcome], pub: BackdatedPublisher, keys: Iterable[str],
                  seconds: float) -> None:
    print(f"\n{len(results)} calls in {seconds:.0f} s "
          f"({len(results) / max(seconds, 1e-9):.1f} calls/s)")
    for k in keys:
        mine = [r for r in results if r.plan.tenant == k]
        if not mine:
            continue
        t = pub.tally[k]
        failed = [r for r in mine if not r.ok]
        print(f"\n  {k}")
        print(f"    calls          {t['call_started']} started, {t['call_ended']} ended, "
              f"{len(failed)} failed")
        print(f"    escalations    {t['escalated']}")
        print(f"    utterances     {t['utterance_transcribed']}  "
              f"(barge-ins {t['barged_in']}, slot rejections {t['slot_rejected']})")
        tools = pub.tools.get(k) or Counter()
        print(f"    tools called   {sum(tools.values())}"
              + (": " + ", ".join(f"{n} {name}" for name, n in sorted(tools.items()))
                 if tools else ""))
        searched = t["knowledge_searched"]
        if searched:
            print(f"    searches       {searched}: {t['search_covered']} covered, "
                  f"{t['search_uncovered']} uncovered "
                  f"({100 * t['search_uncovered'] / searched:.0f}%), {t['search_failed']} failed")
            top = (pub.gaps.get(k) or Counter()).most_common(5)
            if top:
                print("    top gaps       " + "; ".join(f"{q} x{n}" for q, n in top))
        print(f"    errors         {t['error_raised']} error events; {t['events']} events in all")
        for r in failed[:3]:
            print(f"    failed call {r.call_id}: {r.error}")
    print(f"\n  publisher: {pub.stats()}")


# -- main -------------------------------------------------------------------------------


def _per_tenant(spec: str, default: float | None) -> dict[str, float]:
    out = dict(sc.DEFAULT_CALLS_PER_DAY)
    if default is not None:
        out = dict.fromkeys(TENANT_KEYS, default)
    for part in filter(None, (s.strip() for s in spec.split(","))):
        k, _, v = part.partition("=")
        if k not in TENANT_KEYS or not v:
            raise SystemExit(f"--per-tenant: bad entry {part!r} (tenant=calls)")
        out[k] = float(v)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env = os.environ
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--calls-per-day", type=float, default=None,
                    help="weekday mean for every tenant (default per tenant: "
                         + ", ".join(f"{k} {v:g}" for k, v in sc.DEFAULT_CALLS_PER_DAY.items())
                         + "); weekends are quieter")
    ap.add_argument("--per-tenant", default="", help="e.g. acme=55,stockline=30 (overrides)")
    ap.add_argument("--tenants", default=",".join(TENANT_KEYS))
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1126)
    ap.add_argument("--tz", default="Europe/Paris", help="the tenants' local time zone")
    ap.add_argument("--end", default="", help="ISO time the window ends (default: now)")
    ap.add_argument("--limit", type=int, default=0,
                    help="run only N calls, spread over the window (a smoke run)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; touch nothing")
    ap.add_argument("--offline", action="store_true",
                    help="fixture tools, a stand-in search, events counted not published")
    ap.add_argument("--stockline-slug", default="stockline")
    ap.add_argument("--stockline-id", default="",
                    help="the portal tenant UUID; default: resolved from PLATFORM_DATABASE_URL")
    ap.add_argument("--stockline-name", default="Stockline",
                    help="display name, with --stockline-id (or offline)")
    ap.add_argument("--llm-url", default="", help="an external fake LLM; default: in-process")
    ap.add_argument("--llm-model", default="sim-llm")
    ap.add_argument("--ttft-ms-median", type=float, default=300)
    ap.add_argument("--barge-in-rate", type=float, default=0.03)
    ap.add_argument("--knowledge-timeout", type=float, default=5.0,
                    help="seconds per search (live calls get 1.5)")
    ap.add_argument("--knowledge-concurrency", type=int, default=2)
    ap.add_argument("--turn-timeout", type=float, default=30.0)
    ap.add_argument("--schema-timeout", type=float, default=60.0)
    ap.add_argument("--progress-every", type=int, default=100)
    ap.add_argument("--tenants-dir", default=str(TENANTS_DIR))
    ap.add_argument("-v", "--verbose", action="store_true", help="the orchestrator's own logs")
    args = ap.parse_args(argv)
    args.keys = tuple(k.strip() for k in args.tenants.split(",") if k.strip())
    if bad := [k for k in args.keys if k not in TENANT_KEYS]:
        ap.error(f"unknown tenant(s) {bad}; choose from {', '.join(TENANT_KEYS)}")
    args.per_day = _per_tenant(args.per_tenant, args.calls_per_day)
    args.kafka = env.get("KAFKA_BOOTSTRAP", "").strip()
    args.mcp_servers = env.get("MCP_SERVERS", "").strip()
    args.knowledge_url = env.get("KNOWLEDGE_URL", "").strip()
    args.dsn = env.get("PLATFORM_DATABASE_URL", "").strip()
    return args


def plan_from_args(args: argparse.Namespace) -> tuple[PlanConfig, list[CallPlan]]:
    tz = resolve_tz(args.tz)
    now = datetime.fromisoformat(args.end) if args.end else None
    if now is not None and now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    cfg = PlanConfig(tenants=args.keys, days=args.days, per_day=args.per_day, seed=args.seed,
                     tz=tz, now=now, barge_in_rate=args.barge_in_rate)
    plans = build_plan(cfg, sc.Fixtures.load(Path(args.tenants_dir)))
    if args.limit and len(plans) > args.limit:
        step = len(plans) / args.limit
        plans = [plans[int(i * step)] for i in range(args.limit)]
    return cfg, plans


async def main_async(args: argparse.Namespace) -> int:
    cfg, plans = plan_from_args(args)
    print_plan(plans, cfg)
    if args.dry_run:
        return 0

    slugs = {"acme": "acme", "swiftnet": "swiftnet", "stockline": args.stockline_slug}
    fx = sc.Fixtures.load(Path(args.tenants_dir))
    undo_tools: Callable[[], None] | None = None
    if args.offline:
        mcp: dict[str, str] = dict(OFFLINE_MCP)
        undo_tools = install_fixture_tools(fx)
        knowledge: KnowledgeClient | None = OfflineKnowledge()
        pub, _ = memory_publisher(keep=False)
        stockline_id = args.stockline_id or "00000000-0000-4000-8000-00000000c0de"
        dsn = None
    else:
        needs = {"KAFKA_BOOTSTRAP": args.kafka}
        if {"acme", "swiftnet"} & set(args.keys):
            needs["MCP_SERVERS"] = args.mcp_servers
        if "stockline" in args.keys:
            needs["KNOWLEDGE_URL"] = args.knowledge_url
        missing = [name for name, val in needs.items() if not val]
        if missing:
            print(f"missing {', '.join(missing)} (or use --offline / --dry-run)")
            return 2
        mcp = parse_mcp_servers(args.mcp_servers) if args.mcp_servers else {}
        knowledge = BoundedKnowledge(args.knowledge_url, timeout_s=args.knowledge_timeout,
                                     limit=args.knowledge_concurrency) \
            if args.knowledge_url else None
        pub = BackdatedPublisher.from_env(ev.SERVICE_ORCHESTRATOR)
        stockline_id, dsn = args.stockline_id or None, args.dsn or None
        if not await wait_for_schemas(pub, args.schema_timeout):
            print(f"schema registry gave no schema id for {EVENTS_TOPIC} within "
                  f"{args.schema_timeout:.0f} s: nothing would be published")
            return 2

    print("\ntenants:")
    directory = await build_directory(args.keys, slugs, tenants_dir=Path(args.tenants_dir),
                                      stockline_id=stockline_id,
                                      stockline_name=args.stockline_name, dsn=dsn)
    runner = None
    llm_url = args.llm_url
    if not llm_url:
        runner, llm_url = await start_fake_llm(args.ttft_ms_median)
    servicer = OrchestratorServicer(directory, "history-sim", args.llm_model, mcp, pub,
                                    llm_url=llm_url, knowledge=knowledge)

    total, done, failed = len(plans), [0], [0]
    t0 = time.monotonic()

    def progress(res: CallOutcome) -> None:
        done[0] += 1
        failed[0] += not res.ok
        if args.verbose or not res.ok:
            print(f"  [{res.plan.index:5d}] {res.plan.tenant:<9} {res.plan.script.scenario:<26} "
                  f"turns={res.turns} {'ESCALATED ' if res.hung_up_by_agent else ''}{res.error}")
        if done[0] % args.progress_every == 0 or done[0] == total:
            rate = done[0] / max(time.monotonic() - t0, 1e-9)
            day = res.plan.day.isoformat()
            print(f"  {done[0]:>6}/{total} calls ({100 * done[0] / total:3.0f}%) up to {day} | "
                  f"{rate:.1f} calls/s, eta {(total - done[0]) / max(rate, 1e-9) / 60:.1f} min | "
                  f"{failed[0]} failed | events {pub.stats()['sent']} sent", flush=True)

    print(f"\nrunning {total} calls, {args.concurrency} at a time "
          f"({'offline' if args.offline else 'publishing to ' + args.kafka})")
    try:
        results = await run_calls(servicer, pub, plans, slugs, concurrency=args.concurrency,
                                  turn_timeout_s=args.turn_timeout, on_done=progress)
    finally:
        if runner is not None:
            await runner.cleanup()
        await directory.close()
        if undo_tools is not None:
            undo_tools()
    seconds = time.monotonic() - t0
    pub.close(timeout=60.0)
    print_summary(results, pub, args.keys, seconds)
    stats = pub.stats()
    lost = sum(stats[k] for k in ("no_schema", "buffer_full", "delivery_failed", "error"))
    if lost:
        print(f"\nWARNING: {lost} event(s) were not delivered")
    return 1 if failed[0] or lost else 0


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    # Every turn of every call logs at INFO; thousands of calls would drown the progress.
    for name in ("orch", "agent", "tools", "flow", "tenants", "events"):
        logging.getLogger(name).setLevel(logging.INFO if args.verbose else logging.WARNING)
    logging.getLogger("history").setLevel(logging.INFO)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
