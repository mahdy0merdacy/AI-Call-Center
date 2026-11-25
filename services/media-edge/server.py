"""Media edge — the data plane.

Terminates WebRTC, drives STT, synthesises TTS, plays audio back. It holds no
model, no conversation state, and makes no decisions. Everything it does is
transform bytes or obey a directive.

    browser ──WebRTC──▶ edge ──gRPC──▶ STT          (audio)
                          │  ──gRPC──▶ orchestrator (text only)
                          │  ──HTTP──▶ TTS          (audio)
                          └──── data channel ──▶ browser

Why the split: this process carries ~50 audio frames per second and must never
stall. The orchestrator handles ~10 text messages per turn. While dialogue logic
lived here, every prompt change redeployed the component carrying live calls.

    python server.py --port 8082 --stt 127.0.0.1:50051 \\
        --orchestrator 127.0.0.1:50052 --tts http://127.0.0.1:8083

The address and tenant flags default to environment variables (HOST, PORT,
STT_ADDR, ORCHESTRATOR_ADDR, TTS_URL, DEFAULT_TENANT). Containers are configured
by those alone, with service names, so the same image runs in compose and in k8s.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import aiohttp
import av
import grpc
import numpy as np
from aiohttp import web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from gen import orchestrator_pb2 as ob  # noqa: E402
from gen import orchestrator_pb2_grpc as ob_grpc  # noqa: E402
from gen import stt_pb2, stt_pb2_grpc  # noqa: E402

STATIC = HERE / "static"
TARGET_RATE = 16000
# The client picker loads once per page view: a slow orchestrator gets a quick
# 503 the page can show, not a hung request.
LIST_TENANTS_TIMEOUT_S = 2.0

log = logging.getLogger("edge")

# Whisper annotates non-speech in brackets: [BLANK_AUDIO], (camera clicks),
# [INAUDIBLE], (speaking in foreign language). A live test showed these becoming
# real turns — one triggered a second greeting, another burned a slot retry.
_ANNOTATION = re.compile(r"^\s*[\[\(][^\]\)]*[\]\)]\s*$")


class Call:
    """One WebRTC call bridged to STT (audio) and the orchestrator (decisions)."""

    def __init__(self, pc, cfg: dict):
        self.pc = pc
        self.cfg = cfg
        self.channel = None
        self.pending: list[str | bytes] = []    # sent before the channel opened
        self.audio_q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.events_q: asyncio.Queue = asyncio.Queue()
        self.frames = 0
        self.samples_sent = 0
        self.closed = False
        self.ended = False
        self.voice = "Aria"
        self.locale = "en-US"
        self.silence_ms = cfg.get("silence_ms", 900)
        self.t_final: float | None = None

    # -- browser ----------------------------------------------------------
    #
    # The orchestrator's Ready (the "tenant" message) and the first greeting
    # clause can arrive before the browser's data channel has opened: the call
    # starts on the audio track, and SCTP comes up a moment later. Sent then, they
    # were dropped and the page never learned which client it was talking to.
    # Messages are held until the channel opens, then flushed in order.

    PENDING_MAX = 64            # a greeting is a handful of messages; bound a stall

    def attach(self, channel):
        self.channel = channel
        if channel.readyState == "open":
            self._flush()
        else:
            channel.on("open", self._flush)

    def _flush(self):
        pending, self.pending = self.pending, []
        for item in pending:
            self._deliver(item)

    def _deliver(self, item):
        try:
            self.channel.send(item)
        except Exception as exc:
            log.warning("data channel send failed: %s", exc)

    def _send(self, item):
        if self.channel and self.channel.readyState == "open":
            self._deliver(item)
        elif not self.closed and len(self.pending) < self.PENDING_MAX:
            self.pending.append(item)

    def send(self, payload: dict):
        self._send(json.dumps(payload))

    def send_audio(self, wav: bytes):
        self._send(wav)

    # -- audio in ---------------------------------------------------------

    async def consume_track(self, track):
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
        while not self.closed:
            try:
                frame = await track.recv()
            except Exception:
                break
            self.frames += 1
            for out in resampler.resample(frame):
                pcm = out.to_ndarray().reshape(-1).astype(np.int16).tobytes()
                try:
                    self.audio_q.put_nowait(pcm)
                except asyncio.QueueFull:
                    # Bounded: if STT stalls we drop rather than grow unboundedly
                    # and turn a hiccup into an OOM.
                    log.warning("audio queue full — dropping a frame")
        await self.audio_q.put(None)

    # -- STT --------------------------------------------------------------

    async def _stt_requests(self):
        yield stt_pb2.TranscribeRequest(config=stt_pb2.StreamConfig(
            session_id=self.cfg["session_id"],
            language=self.locale.split("-")[0],
            silence_ms_to_endpoint=self.silence_ms,
            min_speech_ms=self.cfg.get("min_speech_ms", 200),
            vad_threshold=self.cfg.get("vad_threshold", 0.5),
            format=stt_pb2.AudioFormat(sample_rate=TARGET_RATE, channels=1,
                                       encoding=stt_pb2.AudioFormat.PCM_S16LE)))
        while True:
            pcm = await self.audio_q.get()
            if pcm is None:
                return
            yield stt_pb2.TranscribeRequest(audio=stt_pb2.AudioChunk(
                pcm=pcm, start_sample=self.samples_sent, sent_unix_ns=time.time_ns()))
            self.samples_sent += len(pcm) // 2

    async def run_stt(self, target: str):
        """Audio -> text. Transcripts become orchestrator events."""
        async with grpc.aio.insecure_channel(target, options=[
            ("grpc.keepalive_time_ms", 20000),
            ("grpc.keepalive_permit_without_calls", 1)]) as ch:
            stub = stt_pb2_grpc.SpeechToTextStub(ch)
            try:
                async for ev in stub.Transcribe(self._stt_requests()):
                    kind = ev.WhichOneof("event")
                    audio_s = round(self.samples_sent / TARGET_RATE, 1)

                    if kind == "speech_start":
                        log.info("speech_start @%.1fs", audio_s)
                        self.send({"type": "speech_start", "audio_s": audio_s})
                        self.events_q.put_nowait(ob.CallEvent(
                            speech_started=ob.SpeechStarted(
                                at_sample=ev.speech_start.at_sample)))

                    elif kind == "partial":
                        self.send({"type": "partial", "text": ev.partial.text,
                                   "delta": ev.partial.delta, "audio_s": audio_s})

                    elif kind == "speech_end":
                        log.info("speech_end after %dms silence", ev.speech_end.silence_ms)
                        self.send({"type": "speech_end",
                                   "silence_ms": ev.speech_end.silence_ms,
                                   "audio_s": audio_s})

                    elif kind == "final":
                        text = ev.final.text.strip()
                        log.info("FINAL: %s", text)
                        self.send({"type": "final", "text": text, "audio_s": audio_s})
                        if _ANNOTATION.match(text) or len(text) < 2:
                            log.info("ignoring non-speech final: %r", text[:40])
                            continue
                        self.t_final = time.perf_counter()
                        self.events_q.put_nowait(ob.CallEvent(
                            transcript=ob.Transcript(text=text, is_final=True,
                                                     at_sample=ev.final.end_sample)))
            except grpc.aio.AioRpcError as e:
                log.error("STT stream failed: %s %s", e.code().name, e.details())
                self.send({"type": "error", "code": e.code().name,
                           "detail": e.details() or ""})
        self.events_q.put_nowait(None)

    # -- orchestrator -----------------------------------------------------

    async def _orch_events(self, slug: str):
        yield ob.CallEvent(start=ob.StartCall(
            tenant_slug=slug, call_id=self.cfg["session_id"]))
        while True:
            ev = await self.events_q.get()
            if ev is None:
                yield ob.CallEvent(end=ob.EndCall(reason="hangup"))
                return
            yield ev

    # A call the orchestrator refuses before it has started (UNAVAILABLE: it is
    # draining for a rollout, or Postgres is briefly unreachable) is retried on a
    # fresh channel, which on k3s lands on another replica. Delays in seconds.
    START_RETRIES = (0.5, 1.5)

    async def run_orchestrator(self, target: str, slug: str, tts_url: str):
        """Directives in, speech out. No audio crosses this stream."""
        for attempt, delay in enumerate((0.0, *self.START_RETRIES)):
            if delay:
                await asyncio.sleep(delay)
            if self.closed:
                break
            started = await self._session_once(target, slug, tts_url,
                                               last_try=attempt == len(self.START_RETRIES))
            if started is not False:
                break
            log.info("orchestrator unavailable before the call started — retrying")
        self.send({"type": "closed"})
        await self.end()

    async def _session_once(self, target: str, slug: str, tts_url: str,
                            last_try: bool) -> bool | None:
        """One Session stream. False = refused before any directive (retryable)."""
        got_directive = False
        async with grpc.aio.insecure_channel(target, options=[
            ("grpc.keepalive_time_ms", 20000),
            ("grpc.keepalive_permit_without_calls", 1)]) as ch:
            stub = ob_grpc.OrchestratorStub(ch)
            async with aiohttp.ClientSession() as http:
                try:
                    async for d in stub.Session(self._orch_events(slug)):
                        got_directive = True
                        await self._apply(d, http, tts_url)
                except grpc.aio.AioRpcError as e:
                    if (e.code() == grpc.StatusCode.UNAVAILABLE and not got_directive
                            and not last_try):
                        return False
                    log.error("orchestrator stream failed: %s %s",
                              e.code().name, e.details())
                    self.send({"type": "error", "code": e.code().name,
                               "detail": e.details() or ""})
        return got_directive

    async def end(self):
        """The orchestrator side is over, so the call is: without it nobody answers.

        Stops STT and closes the peer connection, after giving the last spoken
        reply (already queued on the data channel) a moment to reach the browser.
        """
        if self.closed:
            return
        self.closed = True
        try:
            self.audio_q.put_nowait(None)        # ends the STT stream
        except asyncio.QueueFull:
            pass
        for _ in range(30):                      # at most 3 s for queued audio
            if not self.channel or self.channel.bufferedAmount == 0:
                break
            await asyncio.sleep(0.1)
        await self.pc.close()

    async def _apply(self, d, http, tts_url: str):
        kind = d.WhichOneof("directive")

        if kind == "ready":
            r = d.ready
            self.locale, self.voice = r.locale, r.voice
            self.silence_ms = r.endpointing.silence_ms or self.silence_ms
            log.info("tenant %s (%s) voice=%s", r.display_name, r.locale, r.voice)
            self.send({"type": "tenant", "slug": r.tenant_id, "name": r.display_name,
                       "locale": r.locale, "voice": r.voice,
                       "flows": list(r.flows), "tools": list(r.tools)})

        elif kind == "say":
            # Synthesis is data-plane work — the orchestrator sent text.
            await self._speak(http, tts_url, d.say.text)

        elif kind == "endpointing":
            self.silence_ms = d.endpointing.silence_ms
            log.info("endpointing -> %dms", self.silence_ms)
            self.send({"type": "set_silence_ms", "value": self.silence_ms})

        elif kind == "cancel":
            log.info("cancel (%s) — stop speaking", d.cancel.reason)
            self.send({"type": "barge_in"})

        elif kind == "hangup":
            log.info("hangup: %s", d.hangup.reason)
            self.ended = True
            self.send({"type": "escalated"} if d.hangup.reason == "escalate"
                      else {"type": "closed"})

        elif kind == "turn":
            t = d.turn
            if t.kind == "tool_result":
                log.info("TOOL %s -> %s", t.tool, t.outcome)
                self.send({"type": "tool_result", "tool": t.tool, "outcome": t.outcome})
            else:
                self.send({"type": "flow", "kind": t.kind, "intent": t.intent,
                           "slots": dict(t.slots)})

    async def _speak(self, http, tts_url: str, text: str):
        payload = {"model": "magpie", "input": text, "voice": self.voice,
                   "response_format": "wav", "sample_rate": 22050}
        try:
            async with http.post(f"{tts_url.rstrip('/')}/v1/audio/speech", json=payload,
                                 timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status != 200:
                    log.error("TTS HTTP %s", resp.status)
                    return
                wav = await resp.read()
        except Exception as exc:
            log.error("TTS failed: %s", exc)
            return

        log.info("speaking %d bytes: %s", len(wav), text[:60])
        if self.t_final is not None:
            dt = time.perf_counter() - self.t_final
            log.info("first audio after %.2fs", dt)
            self.send({"type": "first_audio_s", "value": round(dt, 2)})
            self.t_final = None
        self.send({"type": "speaking", "text": text})
        self.send_audio(wav)


async def offer(request):
    app = request.app
    params = await request.json()
    slug = params.get("tenant") or app["default_tenant"]

    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
    call = Call(pc, {"session_id": f"{slug}-{int(time.time() * 1000)}",
                     "min_speech_ms": app["min_speech_ms"],
                     "vad_threshold": app["vad_threshold"],
                     "silence_ms": app["silence_ms"]})
    app["calls"].add(call)

    @pc.on("datachannel")
    def on_datachannel(channel):
        waiting = len(call.pending)
        call.attach(channel)
        log.info("data channel open (%d message(s) were waiting)", waiting)

    @pc.on("connectionstatechange")
    async def on_state():
        log.info("connection %s", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            call.closed = True
            app["calls"].discard(call)   # else /healthz counts ended calls forever

    @pc.on("track")
    def on_track(track):
        if track.kind != "audio":
            return
        log.info("audio track — STT %s | orchestrator %s",
                 app["stt_target"], app["orch_target"])
        asyncio.ensure_future(call.consume_track(track))
        asyncio.ensure_future(call.run_stt(app["stt_target"]))
        asyncio.ensure_future(call.run_orchestrator(
            app["orch_target"], slug, app["tts_url"]))

    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    return web.json_response({"sdp": pc.localDescription.sdp,
                              "type": pc.localDescription.type})


async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def widget(request):
    return web.FileResponse(STATIC / "index.html")


async def tenants(request):
    """Who can be called: the test tenants and the platform's active clients.

    The orchestrator owns tenant resolution; the edge only relays its list. A
    channel per request, like a channel per call: the name is resolved afresh,
    so an orchestrator restarted at a new address is found on the next page load
    instead of after a stale channel's reconnect backoff."""
    target = request.app["orch_target"]
    try:
        async with grpc.aio.insecure_channel(target) as ch:
            resp = await ob_grpc.OrchestratorStub(ch).ListTenants(
                ob.ListTenantsRequest(), timeout=LIST_TENANTS_TIMEOUT_S)
    except grpc.aio.AioRpcError as e:
        # Details carry internal addresses: log them, give the page the code only.
        log.warning("ListTenants via %s failed: %s %s", target, e.code().name, e.details())
        return web.json_response(
            {"error": f"orchestrator ListTenants failed: {e.code().name}"}, status=503)
    return web.json_response(
        {"tenants": [{"slug": t.slug, "display_name": t.display_name, "locale": t.locale,
                      "source": t.source, "documents": t.documents}
                     for t in resp.tenants]},
        headers={"Cache-Control": "no-store"})   # a client who just signed up shows at once


async def healthz(request):
    """Liveness/readiness for k8s. The edge holds no model, so it is ready as soon
    as it listens; the active-call count shows whether a rollout would cut calls."""
    return web.json_response({"status": "ok", "calls": len(request.app["calls"])})


async def on_shutdown(app):
    for call in list(app["calls"]):
        call.closed = True
        await call.pc.close()
    app["calls"].clear()


def _env(name: str, default: str) -> str:
    # Empty counts as unset: manifests often template a variable to "".
    return os.environ.get(name) or default


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    # Env for containers (compose and k8s set service names), flags for a
    # terminal; an explicit flag still wins over the environment.
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=_env("HOST", "127.0.0.1"),
                    help="bind address (env HOST)")
    ap.add_argument("--port", type=int, default=_env("PORT", "8082"),
                    help="listen port (env PORT)")
    ap.add_argument("--stt", default=_env("STT_ADDR", "127.0.0.1:50051"),
                    help="STT gRPC target (env STT_ADDR)")
    ap.add_argument("--orchestrator", default=_env("ORCHESTRATOR_ADDR", "127.0.0.1:50052"),
                    help="orchestrator gRPC target (env ORCHESTRATOR_ADDR)")
    ap.add_argument("--tts", default=_env("TTS_URL", "http://127.0.0.1:8083"),
                    help="TTS base URL (env TTS_URL)")
    ap.add_argument("--default-tenant", default=_env("DEFAULT_TENANT", "acme"),
                    help="tenant for an /offer that names none (env DEFAULT_TENANT)")
    ap.add_argument("--silence-ms", type=int, default=900)
    ap.add_argument("--min-speech-ms", type=int, default=200)
    ap.add_argument("--vad-threshold", type=float, default=0.5)
    return ap.parse_args(argv)


def make_app(args: argparse.Namespace) -> web.Application:
    app = web.Application()
    app["calls"] = set()
    app["stt_target"] = args.stt
    app["orch_target"] = args.orchestrator
    app["tts_url"] = args.tts
    app["default_tenant"] = args.default_tenant
    app["silence_ms"] = args.silence_ms
    app["min_speech_ms"] = args.min_speech_ms
    app["vad_threshold"] = args.vad_threshold
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/w/{slug}", widget)
    app.router.add_get("/tenants", tenants)
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/offer", offer)
    app.router.add_static("/static", STATIC)
    return app


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("aioice").setLevel(logging.WARNING)

    app = make_app(args)
    log.info("STT %s | orchestrator %s | TTS %s", args.stt, args.orchestrator, args.tts)
    log.info("open http://%s:%d", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
