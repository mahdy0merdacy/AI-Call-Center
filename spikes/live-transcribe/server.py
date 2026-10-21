"""Live end to end: browser mic -> WebRTC -> Simul-Whisper -> transcript in the browser.

This joins the two spikes that came before it. spikes/webrtc-audio established what
the audio actually looks like arriving at aiortc (48 kHz stereo s16, 960-sample
frames, 20 ms apart). spikes/simul-whisper established what Simul-Whisper costs
(~110 ms per 1 s chunk on GPU) and that it emits tokens mid-utterance. Here the
frames feed the transcriber and the partial text goes back over the same peer
connection on a data channel.

    python server.py                 # then open http://localhost:8081

Design notes that matter:

  * Inference runs in a thread executor. It takes ~110 ms and recv() delivers a
    frame every 20 ms, so doing it inline would stall the receive loop and back up
    the jitter buffer.
  * The model is warmed at startup. The first inference costs ~630 ms of CUDA
    kernel compilation; paying that on a caller's first word is the same mistake
    the webrtc spike caught in its own instrumentation.
  * PaddedAlignAttWhisper holds per-utterance state (segments, tokens), so it
    handles exactly one call at a time. A second caller is refused rather than
    silently corrupting the first one's transcript.
"""

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from pathlib import Path

import av
import numpy as np
import torch
from aiohttp import web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))  # spikes/ -> simul_compat

from simul_compat import REPO, LiveSegmenter, install_shims  # noqa: E402

install_shims()

from simul_whisper.transcriber.config import AlignAttConfig  # noqa: E402
from simul_whisper.transcriber.simul_whisper import (  # noqa: E402
    DEC_PAD,
    PaddedAlignAttWhisper,
)

STATIC = HERE / "static"
TARGET_RATE = 16000

log = logging.getLogger("live")


class Transcriber:
    """Owns the model and the one-at-a-time inference loop."""

    def __init__(self, cfg, segment_length):
        self.cfg = cfg
        self.segment_length = segment_length
        self.model = PaddedAlignAttWhisper(cfg)
        self.lock = asyncio.Lock()
        self.reset_state()

    def reset_state(self):
        self.tokens = []
        self.text = ""
        self.infer_ms = []
        self.chunks = 0

    def warm_up(self):
        """Pay CUDA kernel compilation before a caller is on the line."""
        t0 = time.perf_counter()
        dummy = torch.zeros(16240, dtype=torch.float32)
        self.model.infer(dummy, False)
        self.model.refresh_segment(complete=True)
        self.reset_state()
        return (time.perf_counter() - t0) * 1000

    def _infer_sync(self, chunk, is_last):
        t0 = time.perf_counter()
        new_toks = self.model.infer(chunk, is_last)
        dt = (time.perf_counter() - t0) * 1000
        self.tokens.append(new_toks)
        hyp = torch.cat(self.tokens, dim=0)
        hyp = hyp[hyp < DEC_PAD]
        return self.model.tokenizer.decode(hyp), dt

    async def transcribe(self, chunk, is_last=False):
        """Returns (full_text, delta, infer_ms) or None when nothing new appeared."""
        loop = asyncio.get_running_loop()
        async with self.lock:
            text, dt = await loop.run_in_executor(None, self._infer_sync, chunk, is_last)
            self.infer_ms.append(dt)
            self.chunks += 1
            if text == self.text:
                return None
            delta, self.text = text[len(self.text):], text
            return text, delta, dt

    def finish(self):
        self.model.refresh_segment(complete=True)
        stats = {
            "chunks": self.chunks,
            "infer_p50_ms": round(statistics.median(self.infer_ms), 1) if self.infer_ms else None,
            "infer_max_ms": round(max(self.infer_ms), 1) if self.infer_ms else None,
        }
        self.reset_state()
        return stats


class Session:
    """One call: audio in, transcript out over the data channel."""

    def __init__(self, pc, transcriber, segment_length):
        self.pc = pc
        self.tx = transcriber
        self.segmenter = LiveSegmenter(segment_length)
        self.channel = None
        self.queue = asyncio.Queue()
        self.frames = 0
        self.t_first_frame = None
        self.closed = False

    def send(self, payload):
        if self.channel and self.channel.readyState == "open":
            try:
                self.channel.send(json.dumps(payload))
            except Exception as exc:
                log.warning("data channel send failed: %s", exc)

    async def consume_audio(self, track):
        """recv() -> 16 kHz mono float32 -> segmenter -> queue. Never blocks on the model."""
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
        while not self.closed:
            try:
                frame = await track.recv()
            except Exception:
                break
            if self.t_first_frame is None:
                self.t_first_frame = time.perf_counter()
            self.frames += 1

            for out in resampler.resample(frame):
                mono = out.to_ndarray().reshape(-1).astype(np.float32) / 32768.0
                for chunk in self.segmenter.feed(mono):
                    self.queue.put_nowait((chunk, False))

        # Flush the partial chunk with is_last=True. Without that flag the
        # attention-guided truncation keeps holding the final tokens back and the
        # last word of every call is silently lost.
        tail = self.segmenter.flush()
        if tail is not None and len(tail) > 0:
            self.queue.put_nowait((tail, True))
        else:
            self.queue.put_nowait((torch.zeros(self.segmenter.buffer_len), True))
        self.queue.put_nowait(None)  # sentinel

    async def run_transcription(self):
        while True:
            item = await self.queue.get()
            if item is None:
                break
            chunk, is_last = item
            backlog = self.queue.qsize()
            result = await self.tx.transcribe(chunk, is_last=is_last)
            if result:
                text, delta, dt = result
                log.info("+%r  (%.0f ms, backlog %d)", delta, dt, backlog)
                self.send({
                    "type": "partial",
                    "text": text,
                    "delta": delta,
                    "infer_ms": round(dt),
                    "backlog_chunks": backlog,
                    "audio_s": round(self.frames * 0.02, 1),
                })
            else:
                self.send({
                    "type": "tick",
                    "infer_ms": round(self.tx.infer_ms[-1]) if self.tx.infer_ms else None,
                    "backlog_chunks": backlog,
                    "audio_s": round(self.frames * 0.02, 1),
                })

        log.info("FINAL TRANSCRIPT: %s", self.tx.text.strip())
        stats = self.tx.finish()
        self.send({"type": "done", **stats})
        log.info("call finished: %s", stats)


async def offer(request):
    app = request.app
    current = app["state"]["session"]
    if current is not None and not current.closed:
        return web.json_response(
            {"error": "busy: this spike transcribes one call at a time"}, status=409
        )

    params = await request.json()
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
    session = Session(pc, app["transcriber"], app["segment_length"])
    app["state"]["session"] = session

    @pc.on("datachannel")
    def on_datachannel(channel):
        session.channel = channel
        log.info("data channel open: %s", channel.label)
        session.send({"type": "ready", "segment_length": app["segment_length"],
                      "device": str(app["transcriber"].model.model.device)})

    @pc.on("connectionstatechange")
    async def on_state():
        log.info("connection %s", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            session.closed = True

    @pc.on("track")
    def on_track(track):
        if track.kind != "audio":
            return
        log.info("audio track received")
        asyncio.ensure_future(session.consume_audio(track))
        asyncio.ensure_future(session.run_transcription())

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    return web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
    )


async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def on_shutdown(app):
    s = app["state"]["session"]
    if s:
        s.closed = True
        await s.pc.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--model-path", default=str(Path.home() / ".cache/whisper/base.pt"))
    ap.add_argument("--cif", default=None)
    ap.add_argument("--segment-length", type=float, default=1.0)
    ap.add_argument("--frame-threshold", type=int, default=12)
    ap.add_argument("--buffer-len", type=float, default=20.0)
    ap.add_argument("--language", default="en")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("aioice").setLevel(logging.WARNING)

    cif = args.cif or str(REPO / "cif_models" / f"{Path(args.model_path).stem}.pt")
    cfg = AlignAttConfig(
        model_path=args.model_path,
        segment_length=args.segment_length,
        frame_threshold=args.frame_threshold,
        language=args.language,
        buffer_len=args.buffer_len,
        min_seg_len=0.0,
        if_ckpt_path=cif,
    )

    log.info("loading model (%s)…", args.model_path)
    t0 = time.perf_counter()
    transcriber = Transcriber(cfg, args.segment_length)
    log.info("loaded in %.1fs on %s", time.perf_counter() - t0, transcriber.model.model.device)
    log.info("warming up… %.0f ms", transcriber.warm_up())

    app = web.Application()
    app["transcriber"] = transcriber
    app["segment_length"] = args.segment_length
    app["state"] = {"session": None}
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_static("/static", STATIC)

    log.info("open http://%s:%d", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
