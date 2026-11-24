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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
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

    def send(self, payload: dict):
        if self.channel and self.channel.readyState == "open":
            try:
                self.channel.send(json.dumps(payload))
            except Exception as exc:
                log.warning("data channel send failed: %s", exc)

    def send_audio(self, wav: bytes):
        if self.channel and self.channel.readyState == "open":
            try:
                self.channel.send(wav)
            except Exception as exc:
                log.warning("audio send failed: %s", exc)

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

    async def run_orchestrator(self, target: str, slug: str, tts_url: str):
        """Directives in, speech out. No audio crosses this stream."""
        async with grpc.aio.insecure_channel(target, options=[
            ("grpc.keepalive_time_ms", 20000),
            ("grpc.keepalive_permit_without_calls", 1)]) as ch:
            stub = ob_grpc.OrchestratorStub(ch)
            async with aiohttp.ClientSession() as http:
                try:
                    async for d in stub.Session(self._orch_events(slug)):
                        await self._apply(d, http, tts_url)
                except grpc.aio.AioRpcError as e:
                    log.error("orchestrator stream failed: %s %s",
                              e.code().name, e.details())
                    self.send({"type": "error", "code": e.code().name,
                               "detail": e.details() or ""})
        self.send({"type": "closed"})

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
        call.channel = channel
        log.info("data channel open")

    @pc.on("connectionstatechange")
    async def on_state():
        log.info("connection %s", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            call.closed = True

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


async def on_shutdown(app):
    for call in list(app["calls"]):
        call.closed = True
        await call.pc.close()
    app["calls"].clear()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--stt", default="127.0.0.1:50051")
    ap.add_argument("--orchestrator", default="127.0.0.1:50052")
    ap.add_argument("--tts", default="http://127.0.0.1:8083")
    ap.add_argument("--default-tenant", default="acme")
    ap.add_argument("--silence-ms", type=int, default=900)
    ap.add_argument("--min-speech-ms", type=int, default=200)
    ap.add_argument("--vad-threshold", type=float, default=0.5)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("aioice").setLevel(logging.WARNING)

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
    app.router.add_post("/offer", offer)
    app.router.add_static("/static", STATIC)

    log.info("STT %s | orchestrator %s | TTS %s", args.stt, args.orchestrator, args.tts)
    log.info("open http://%s:%d", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
