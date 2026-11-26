"""The control plane.

One bidirectional gRPC stream per call. The media edge sends events; this sends
directives. **No audio crosses this boundary** — the edge terminates WebRTC,
drives STT, and calls TTS itself. Here we handle roughly ten messages per turn
against the edge's fifty audio frames per second.

What that buys, concretely: a prompt change redeploys this and nothing else.
Live calls keep flowing because the thing carrying media never restarted.

    python -m app.server --port 50052

Configured from the environment, so one image runs unchanged under Compose and
k3s. Every flag defaults to its variable:

    HOST, PORT               where to listen (0.0.0.0:50052)
    GROQ_API_KEY             the LLM key (a Secret in k8s)
    LLM_URL, LLM_MODEL       any OpenAI-compatible chat completions endpoint
    TENANTS_DIR              the test tenants (tenants/*.yaml)
    PLATFORM_DATABASE_URL    the portal's Postgres, as voice_reader; unset = test tenants only
    KNOWLEDGE_URL            the knowledge service; unset = no retrieval
    MCP_SERVERS              tool=url,...; unset = the demo MCP servers on 127.0.0.1
    SHUTDOWN_GRACE_S         how long live calls get to finish after SIGTERM (25)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_health.v1.health import aio as health_aio

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
# Shared libraries: <repo>/libs locally, /srv/aicc/libs in the image.
sys.path.insert(0, str(_HERE.parents[3] / "libs"))

from aicc_events import CallEvents, EventPublisher, ev, new_ulid  # noqa: E402

from app.agent import GROQ_URL, Agent  # noqa: E402
from app.flow import Tenant, load_tenants  # noqa: E402
from app.knowledge import KnowledgeClient  # noqa: E402
from app.tenants import (  # noqa: E402
    DirectoryUnavailable,
    PostgresPlatformSource,
    TenantDirectory,
)
from app.tools import ToolRegistry, parse_mcp_servers  # noqa: E402
from gen import orchestrator_pb2 as pb  # noqa: E402
from gen import orchestrator_pb2_grpc as pb_grpc  # noqa: E402

log = logging.getLogger("orch")
SERVICE_NAME = "aicallcenter.orchestrator.v1.Orchestrator"
SHUTTING_DOWN = "orchestrator is shutting down; start the call again"
# After the drain: a moment for an in-flight ListTenants. Calls still open are cut.
STOP_GRACE_S = 1.0


class Session:
    """One call. Holds the conversation — the state worth surviving a data-plane
    restart, which is exactly why it lives here and not in the edge."""

    def __init__(self, tenant, agent: Agent, call_id: str, events: CallEvents):
        self.tenant = tenant
        self.agent = agent
        self.call_id = call_id
        self.events = events
        self.out: asyncio.Queue = asyncio.Queue()
        self.seq = 0
        self.turn: asyncio.Task | None = None
        self.ended = False
        self.started = time.monotonic()

    def emit(self, directive):
        self.out.put_nowait(directive)

    def say(self, text: str):
        if not text.strip():
            return
        self.seq += 1
        self.emit(pb.Directive(say=pb.Say(text=text, seq=self.seq)))

    def on_event(self, e: dict):
        """Agent progress -> directives to the edge, log lines, and call events."""
        t = e.get("type")
        turn = e.get("turn", 0)
        if t == "flow":
            log.info("[%s] flow kind=%s intent=%s slots=%s", self.call_id,
                     e.get("kind"), e.get("intent"), e.get("slots"))
            self.emit(pb.Directive(turn=pb.TurnInfo(
                kind=e.get("kind") or "", intent=e.get("intent") or "",
                slots={k: str(v) for k, v in (e.get("slots") or {}).items()})))
            decided = ev.TurnDecided(turn_index=turn, kind=_TURN_KIND.get(e.get("kind") or "", 0),
                                     intent=e.get("intent") or "")
            if e.get("route_score") is not None:
                decided.route_score = e["route_score"]
            self.events.emit(turn_decided=decided)
        elif t == "slot":
            if e["ok"]:
                self.events.emit(slot_filled=ev.SlotFilled(
                    turn_index=turn, intent=e.get("intent") or "", slot=e["slot"],
                    value=e["value"], attempt=e["attempt"]))
            else:
                self.events.emit(slot_rejected=ev.SlotRejected(
                    turn_index=turn, intent=e.get("intent") or "", slot=e["slot"],
                    attempt=e["attempt"], max_attempts=e["max_attempts"],
                    utterance=e["utterance"]))
        elif t == "tool_result":
            log.info("[%s] TOOL %s -> %s", self.call_id, e.get("tool"), e.get("outcome"))
            self.emit(pb.Directive(turn=pb.TurnInfo(
                kind="tool_result", tool=e.get("tool") or "",
                outcome=e.get("outcome") or "")))
            self.events.emit(tool_invoked=ev.ToolInvoked(
                turn_index=turn, tool=e.get("tool") or "", outcome=e.get("outcome") or "",
                latency_ms=e.get("latency_ms", 0), timed_out=e.get("timed_out", False),
                result_json=json.dumps(e.get("data") or {}, ensure_ascii=False)))
        elif t == "set_silence_ms":
            # Policy pushed down: a code read aloud needs a longer window.
            self.emit(pb.Directive(endpointing=pb.SetEndpointing(
                silence_ms=int(e["value"]))))
            self.events.emit(endpointing_changed=ev.EndpointingChanged(
                turn_index=turn, silence_ms=int(e["value"]), slot=e.get("slot") or ""))
        elif t == "llm":
            self.events.emit(llm_completed=ev.LlmCompleted(
                turn_index=turn, model=e.get("model") or "", purpose=e.get("purpose") or "",
                ttft_ms=e.get("ttft_ms", 0), total_ms=e.get("total_ms", 0),
                prompt_tokens=e.get("prompt_tokens", 0),
                completion_tokens=e.get("completion_tokens", 0),
                cancelled=e.get("cancelled", False)))
        elif t == "escalated":
            log.info("[%s] ESCALATED", self.call_id)
            self.emit(pb.Directive(hangup=pb.Hangup(reason="escalate")))
            self.ended = True
            self.events.emit(escalated=ev.Escalated(
                turn_index=turn, intent=e.get("intent") or "", reason=e.get("reason") or ""))
        elif t == "turn_complete":
            log.info("[%s] turn done in %.2fs | %s", self.call_id,
                     e.get("total_s", 0), (e.get("text") or "")[:80])
            self.events.emit(turn_completed=ev.TurnCompleted(
                turn_index=turn, total_ms=e.get("total_ms", 0), clauses=e.get("clauses", 0),
                spoken_text=e.get("text") or ""))
        elif t == "retrieval":
            if e.get("ok"):
                log.info("[%s] retrieval: %d hit(s) in %d ms (search %d ms)", self.call_id,
                         e.get("hits", 0), e.get("latency_ms", 0), e.get("took_ms", 0))
            else:
                log.warning("[%s] retrieval failed after %d ms: %s", self.call_id,
                            e.get("latency_ms", 0), e.get("error") or "unknown")
            if e.get("query"):    # an empty utterance searched nothing: no fact to record
                self.events.emit(knowledge_searched=ev.KnowledgeSearched(
                    turn_index=turn, query=e["query"], hits=e.get("hits", 0),
                    best_similarity=e.get("best_similarity", 0.0),
                    covered=e.get("covered", False),
                    document_titles=e.get("titles") or [],
                    latency_ms=e.get("latency_ms", 0), failed=not e.get("ok")))
        elif t == "error":
            log.error("[%s] turn error: %s", self.call_id, e.get("detail"))
            self.events.emit(error_raised=ev.ErrorRaised(
                component=e.get("component") or "turn", code=e.get("code") or "",
                detail=e.get("detail") or ""))

    def end(self, reason: int):
        self.events.emit(call_ended=ev.CallEnded(
            reason=reason, duration_ms=int((time.monotonic() - self.started) * 1000),
            turns=self.agent.turn_index))


# Agent decision kinds -> schema enum. "ready_for_tool" is the grounded path.
_TURN_KIND = {"say": ev.TURN_KIND_SAY, "ready_for_tool": ev.TURN_KIND_READY_FOR_TOOL,
              "open": ev.TURN_KIND_OPEN, "escalate": ev.TURN_KIND_ESCALATE}


class OrchestratorServicer(pb_grpc.OrchestratorServicer):
    def __init__(self, tenants: TenantDirectory | Mapping[str, Tenant], groq_key, model,
                 mcp_endpoints, publisher: EventPublisher | None = None,
                 llm_url: str = GROQ_URL, knowledge: KnowledgeClient | None = None):
        # A plain {slug: Tenant} still works: the test tenants alone, as before.
        self.tenants = (tenants if isinstance(tenants, TenantDirectory)
                        else TenantDirectory(tenants))
        self.groq_key = groq_key
        self.model = model
        self.llm_url = llm_url
        self.mcp_endpoints = mcp_endpoints
        self.publisher = publisher or EventPublisher(ev.SERVICE_ORCHESTRATOR)
        self.knowledge = knowledge
        self.active: dict[str, Session] = {}
        self.draining = False       # SIGTERM: new calls are refused, live ones finish
        self.stopping = False       # the drain is over: calls still open get cut

    async def ListTenants(self, request, context):  # noqa: N802
        """The test page's client picker. A directory, not an authorisation
        boundary: it holds public slugs and names, like the /w/<slug> URLs."""
        return pb.ListTenantsResponse(tenants=[
            pb.TenantSummary(slug=s.slug, display_name=s.display_name, locale=s.locale,
                             source=s.source, documents=s.documents)
            for s in await self.tenants.list()
        ])

    async def drain(self, grace_s: float, interrupted: asyncio.Event | None = None) -> int:
        """Refuse new calls, and give the live ones up to grace_s to end on their own.

        Returns how many are still open. A second signal (interrupted) ends the
        wait early: an operator pressing Ctrl-C twice means it.
        """
        self.draining = True
        deadline = time.monotonic() + grace_s
        report_at = time.monotonic() + 5.0      # shutdown() has logged the start
        while self.active and not (interrupted and interrupted.is_set()):
            now = time.monotonic()
            if now >= deadline:
                break
            if now >= report_at:
                log.info("draining: %d call(s) still active, %.0f s left",
                         len(self.active), deadline - now)
                report_at = now + 5.0
            await asyncio.sleep(min(0.1, deadline - now))
        return len(self.active)

    async def Session(self, request_iterator, context):  # noqa: N802
        session: Session | None = None
        pump: asyncio.Task | None = None
        end_reason = ev.END_REASON_CALLER_HUNG_UP   # stream closed by the edge

        async def read_events():
            nonlocal session
            async for msg in request_iterator:
                which = msg.WhichOneof("event")

                if which == "start":
                    slug = msg.start.tenant_slug
                    if self.draining:
                        await context.abort(grpc.StatusCode.UNAVAILABLE, SHUTTING_DOWN)
                    try:
                        tenant = await self.tenants.get(slug)
                    except DirectoryUnavailable as exc:
                        log.warning("call refused: %s", exc)
                        await context.abort(grpc.StatusCode.UNAVAILABLE,
                                            "tenant directory unavailable")
                    if tenant is None:
                        await context.abort(grpc.StatusCode.NOT_FOUND,
                                            f"unknown tenant {slug!r}")
                    # Again: a drain may have begun during the lookup, and a call
                    # admitted now would be cut at the end of it, not drained.
                    if self.draining:
                        await context.abort(grpc.StatusCode.UNAVAILABLE, SHUTTING_DOWN)
                    call_id = msg.start.call_id or new_ulid()
                    registry = ToolRegistry(
                        servers={t: self.mcp_endpoints[t] for t in tenant.tools
                                 if t in self.mcp_endpoints},
                        allowed=set(tenant.tools),
                    )
                    agent = Agent(self.groq_key, self.model, tenant, registry,
                                  llm_url=self.llm_url, knowledge=self.knowledge)
                    session = Session(tenant, agent, call_id,
                                      self.publisher.for_call(call_id, tenant.tenant_id))
                    self.active[call_id] = session
                    session.events.emit(call_started=ev.CallStarted(
                        tenant_slug=slug, locale=tenant.locale,
                        caller_ref=msg.start.caller_ref, synthetic=msg.start.synthetic,
                        channel=msg.start.channel))
                    log.info("call %s opened for %s (%s) — %d active",
                             call_id, slug, tenant.locale, len(self.active))

                    ep = tenant.endpointing or {}
                    session.emit(pb.Directive(ready=pb.Ready(
                        tenant_id=tenant.tenant_id, display_name=tenant.display_name,
                        locale=tenant.locale,
                        voice=(tenant.voice or {}).get("speaker", "Aria"),
                        endpointing=pb.SetEndpointing(
                            silence_ms=ep.get("silence_ms", 900),
                            min_speech_ms=ep.get("min_speech_ms", 200),
                            vad_threshold=0.5),
                        flows=[f.intent for f in tenant.flows if not f.internal],
                        tools=list(tenant.tools))))
                    # greeting goes out as Say clauses; the edge synthesises
                    asyncio.ensure_future(agent.greet(session.on_event, session.say))
                    continue

                if session is None:
                    await context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                                        "first event must be start")

                if which == "transcript" and msg.transcript.is_final:
                    if session.ended or getattr(session.agent, "ended", False):
                        log.info("[%s] call ended — ignoring transcript", session.call_id)
                        continue
                    # What the caller said, as STT finalised it. The schema files
                    # this under speech, but the orchestrator is the one producer
                    # that sees every final, so it records it: the sentiment
                    # worker classifies these, and silver builds turns from them.
                    session.events.emit(utterance_transcribed=ev.UtteranceTranscribed(
                        text=msg.transcript.text, end_sample=msg.transcript.at_sample,
                        language=session.tenant.language))
                    if session.turn and not session.turn.done():
                        session.turn.cancel()
                    session.turn = asyncio.ensure_future(
                        session.agent.respond(msg.transcript.text,
                                              session.on_event, session.say))

                elif which == "speech_started":
                    # Barge-in: the caller talked over us. Stop the reply and tell
                    # the edge to drop queued audio.
                    if session.turn and not session.turn.done():
                        log.info("[%s] barge-in", session.call_id)
                        session.agent.cancelled = True
                        session.emit(pb.Directive(cancel=pb.Cancel(reason="barge_in")))
                        session.events.emit(barged_in=ev.BargedIn(
                            turn_index=session.agent.turn_index,
                            at_sample=msg.speech_started.at_sample))

                elif which == "end":
                    log.info("[%s] call ended: %s", session.call_id, msg.end.reason)
                    break

            session and session.emit(None)  # sentinel closes the writer

        pump = asyncio.ensure_future(read_events())
        try:
            while True:
                if session is None:
                    await asyncio.sleep(0.01)
                    if pump.done():
                        break
                    continue
                directive = await session.out.get()
                if directive is None:
                    break
                yield directive
        except Exception:
            end_reason = ev.END_REASON_ERROR
            raise
        except asyncio.CancelledError:
            # Cut because the drain ran out, not hung up by the caller.
            if self.stopping:
                end_reason = ev.END_REASON_ERROR
            raise
        finally:
            if pump and not pump.done():
                pump.cancel()
            elif pump and not pump.cancelled() and (exc := pump.exception()) is not None:
                # A refused call ends with an abort raised in the reader; anything
                # else would otherwise only surface as "exception never retrieved".
                if not isinstance(exc, grpc.aio.AbortError):
                    log.error("event reader failed", exc_info=exc)
            if session:
                # The caller is gone: stop the reply in progress. Otherwise it keeps
                # streaming LLM tokens nobody will hear, and its events would land
                # after CallEnded.
                turn = session.turn
                if turn and not turn.done():
                    turn.cancel()
                    try:
                        await asyncio.wait({turn}, timeout=1.0)
                    except asyncio.CancelledError:
                        pass    # we are being cancelled too; the original error resumes
                session.end(ev.END_REASON_ESCALATED if session.ended else end_reason)
                self.active.pop(session.call_id, None)
                log.info("[%s] closed — %d active", session.call_id, len(self.active))


async def start_server(servicer: OrchestratorServicer, host: str, port: int):
    """Bind, start and report SERVING. Returns (server, health, the port bound):
    port 0 binds any free port, which is what the tests use."""
    server = grpc.aio.server(options=[
        ("grpc.keepalive_time_ms", 20000),
        ("grpc.keepalive_timeout_ms", 10000),
        ("grpc.keepalive_permit_without_calls", 1),
    ])
    pb_grpc.add_OrchestratorServicer_to_server(servicer, server)
    health = health_aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health, server)
    bound = server.add_insecure_port(f"{host}:{port}")
    await server.start()
    await health.set(SERVICE_NAME, health_pb2.HealthCheckResponse.SERVING)
    await health.set("", health_pb2.HealthCheckResponse.SERVING)
    return server, health, bound


async def shutdown(server, health, servicer: OrchestratorServicer, grace_s: float,
                   interrupted: asyncio.Event | None = None) -> None:
    """SIGTERM: stop being routed to, let live calls finish, then stop.

    Health goes NOT_SERVING first (every service name), so Kubernetes takes the
    pod out of the Service and no new call is routed here. One that still arrives
    (endpoint updates are not instant, and an open HTTP/2 connection outlives
    them) is refused with UNAVAILABLE, which the edge can retry. Live calls get
    grace_s to end on their own; what is left after that is cut.
    """
    await health.enter_graceful_shutdown()
    log.info("draining: %d call(s) active, waiting up to %.0f s",
             len(servicer.active), grace_s)
    left = await servicer.drain(grace_s, interrupted)
    if left:
        log.warning("drain over: cutting %d call(s) still active", left)
    servicer.stopping = True
    await server.stop(grace=STOP_GRACE_S)


async def serve(args):
    tenants = load_tenants()
    if not tenants:
        log.error("no tenant configs found")

    # Blocking file reads are fine here: startup, before the server takes calls.
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key and Path(args.env).exists():  # noqa: ASYNC240
        for line in Path(args.env).read_text().splitlines():  # noqa: ASYNC240
            if line.startswith("GROQ_API_KEY="):
                groq_key = line.split("=", 1)[1].strip()
    if not groq_key:
        log.warning("no GROQ_API_KEY — the agent cannot reply")

    mcp = args.mcp_endpoints
    log.info("MCP tools: %s", ", ".join(f"{tool} -> {_for_log(url)}"
                                        for tool, url in mcp.items()) or "none")
    for t in tenants.values():
        if missing := [tool for tool in t.tools if tool not in mcp]:
            log.warning("tenant %s: no MCP endpoint for %s", t.slug, ", ".join(missing))

    # Env only, no flag: the DSN carries a password, and flags show up in `ps`.
    dsn = os.environ.get("PLATFORM_DATABASE_URL", "").strip()
    platform = PostgresPlatformSource(dsn) if dsn else None
    if platform:
        await platform.open()       # does not wait: test tenants work without Postgres
        log.info("platform tenants: Postgres %s", platform.target)
    else:
        log.info("platform tenants: off (PLATFORM_DATABASE_URL not set)")
    directory = TenantDirectory(tenants, platform)

    knowledge = KnowledgeClient(args.knowledge_url) if args.knowledge_url else None
    log.info("retrieval: %s", _for_log(args.knowledge_url) if knowledge
             else "off (KNOWLEDGE_URL not set)")

    publisher = EventPublisher.from_env(ev.SERVICE_ORCHESTRATOR)
    servicer = OrchestratorServicer(directory, groq_key, args.llm_model, mcp, publisher,
                                    llm_url=args.llm_url, knowledge=knowledge)
    server, health, port = await start_server(servicer, args.host, args.port)
    log.info("orchestrator on %s:%d | LLM %s | %d test tenant(s)",
             args.host, port, args.llm_model, len(tenants))

    stop, interrupted = asyncio.Event(), asyncio.Event()

    def on_signal():
        # The first signal drains; a second one cuts the drain short.
        (interrupted if stop.is_set() else stop).set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_signal)
    await stop.wait()
    await shutdown(server, health, servicer, args.shutdown_grace, interrupted)
    # After the calls drain: their CallEnded events are queued by now.
    publisher.close(timeout=5.0)
    await directory.close()
    log.info("stopped")


def mcp_endpoints(args: argparse.Namespace) -> dict[str, str]:
    """tool -> MCP URL. MCP_SERVERS (--mcp-servers) wins when set; otherwise the
    two demo servers' flags, which default to the local setup on 127.0.0.1."""
    if args.mcp_servers.strip():
        return parse_mcp_servers(args.mcp_servers)
    return {"lookup_shipment": args.mcp_acme,
            "reschedule_delivery": args.mcp_acme,
            "check_line_status": args.mcp_swiftnet}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Flags, each defaulting to its environment variable (see the module doc).

    `or`, not a get() default: an empty variable, as Compose sets for an unset
    one, means the default too.
    """
    env = os.environ
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=env.get("HOST") or "0.0.0.0")
    ap.add_argument("--port", type=int, default=env.get("PORT") or "50052")
    ap.add_argument("--llm-model", default=env.get("LLM_MODEL") or "qwen/qwen3.8-27b")
    # Any OpenAI-compatible chat completions endpoint. The call simulator points
    # this at its fake LLM, so load tests are free, fast and repeatable.
    ap.add_argument("--llm-url", default=env.get("LLM_URL") or GROQ_URL)
    ap.add_argument("--mcp-servers", default=env.get("MCP_SERVERS") or "",
                    help="tool=url,... ; when set, replaces --mcp-acme/--mcp-swiftnet")
    ap.add_argument("--mcp-acme", default="http://127.0.0.1:9101/mcp")
    ap.add_argument("--mcp-swiftnet", default="http://127.0.0.1:9102/mcp")
    ap.add_argument("--knowledge-url", default=(env.get("KNOWLEDGE_URL") or "").strip(),
                    help="knowledge service base URL; empty = no retrieval")
    ap.add_argument("--shutdown-grace", type=float,
                    default=env.get("SHUTDOWN_GRACE_S") or "25",
                    help="seconds live calls get to finish after SIGTERM")
    # GROQ_API_KEY comes from the environment (a Secret in k8s). The file is a
    # local-dev convenience only; the image never ships one.
    ap.add_argument("--env", default=str(Path(__file__).resolve().parents[3] / ".env"),
                    help="dev fallback file holding GROQ_API_KEY; ignored if the "
                         "env var is set")
    args = ap.parse_args(argv)
    try:
        args.mcp_endpoints = mcp_endpoints(args)
    except ValueError as exc:
        ap.error(str(exc))
    if args.knowledge_url and not _is_http_url(args.knowledge_url):
        ap.error("KNOWLEDGE_URL (--knowledge-url) must be an http(s) URL")
    if args.shutdown_grace < 0:
        ap.error("SHUTDOWN_GRACE_S (--shutdown-grace) cannot be negative")
    return args


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _for_log(url: str) -> str:
    """scheme://host:port/path: credentials and query strings stay out of logs."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid URL>"
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{parts.path}"


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
