"""gRPC front door for the STT service.

One process per GPU. It holds ONE shared model and a pool of lightweight call
sessions — not one model per call, because k8s hands out whole GPUs and a second
model instance would waste 925 MiB for nothing.

Operational rules baked in here, each of which exists because of something
measured in spikes/:

  * readiness is gated on model-loaded-AND-warm. The first inference costs
    ~800 ms of CUDA kernel compilation; if readiness passes before that, the
    first caller on a fresh pod wears it.
  * admission control is explicit. Capacity is compute-bound (~200 ms of GPU per
    call per 0.5 s of audio), so past a few concurrent calls everyone degrades
    together. Refusing the (N+1)th call with RESOURCE_EXHAUSTED lets the
    orchestrator route elsewhere; queueing silently would just hide it.
  * shutdown drains. k8s will kill this pod mid-call.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_health.v1.health import aio as health_aio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import SharedEngine  # noqa: E402
from app.gen import stt_pb2, stt_pb2_grpc  # noqa: E402
from app.session import CallSession  # noqa: E402

log = logging.getLogger("stt")

SERVICE_NAME = "aicallcenter.stt.v1.SpeechToText"


class SpeechToTextServicer(stt_pb2_grpc.SpeechToTextServicer):
    def __init__(self, engine: SharedEngine, max_concurrent: int):
        self.engine = engine
        self.max_concurrent = max_concurrent
        self._active: dict[str, CallSession] = {}
        self._draining = False

    @property
    def active(self) -> int:
        return len(self._active)

    async def Transcribe(self, request_iterator, context):
        if self._draining:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "draining")
        if self.active >= self.max_concurrent:
            # Explicit refusal beats unbounded queueing: the orchestrator can
            # pick another worker, and the callers already connected keep their
            # latency budget.
            await context.abort(
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                f"at capacity ({self.active}/{self.max_concurrent})",
            )

        session: CallSession | None = None
        session_id = "?"
        try:
            async for req in request_iterator:
                which = req.WhichOneof("payload")

                if which == "config":
                    if session is not None:
                        await context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                                            "config already sent")
                    cfg = req.config
                    session_id = cfg.session_id or f"anon-{int(time.time()*1000)}"
                    fmt = cfg.format
                    if fmt.sample_rate and fmt.sample_rate != 16000:
                        await context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                                            "sample_rate must be 16000")
                    if fmt.channels and fmt.channels != 1:
                        await context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                                            "channels must be 1")
                    session = CallSession(
                        self.engine, session_id,
                        language=cfg.language or None,
                        silence_ms_to_endpoint=cfg.silence_ms_to_endpoint or 900,
                        min_speech_ms=cfg.min_speech_ms or 200,
                        vad_threshold=cfg.vad_threshold or 0.5,
                    )
                    self._active[session_id] = session
                    log.info("session %s opened, language=%s (%d/%d active)",
                             session_id, session.language,
                             self.active, self.max_concurrent)
                    yield stt_pb2.TranscribeEvent(ready=stt_pb2.Ready(
                        model=self.engine.model_name,
                        device=self.engine.device,
                        segment_length_s=self.engine.segment_length,
                    ))
                    continue

                if session is None:
                    await context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                                        "first message must be config")

                if which == "audio":
                    gap = session.note_gap(req.audio.start_sample)
                    if gap:
                        log.warning("session %s: %d-sample gap in audio", session_id, gap)
                    for ev in await session.feed(req.audio.pcm):
                        yield _to_proto(ev, self.active)

                elif which == "end_utterance":
                    for ev in await session.force_end():
                        yield _to_proto(ev, self.active)

        except asyncio.CancelledError:
            log.info("session %s cancelled", session_id)
            raise
        finally:
            if session is not None:
                self._active.pop(session_id, None)
                log.info("session %s closed: %s", session_id, session.metrics())

    async def drain(self, timeout: float = 30.0):
        self._draining = True
        deadline = time.monotonic() + timeout
        while self._active and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        if self._active:
            log.warning("drain timed out with %d call(s) still active", self.active)


def _to_proto(ev, active: int) -> stt_pb2.TranscribeEvent:
    if ev.kind == "partial":
        return stt_pb2.TranscribeEvent(
            partial=stt_pb2.Partial(text=ev.text, delta=ev.delta))
    if ev.kind == "final":
        return stt_pb2.TranscribeEvent(final=stt_pb2.Final(
            text=ev.text,
            start_sample=ev.extra.get("start_sample", 0),
            end_sample=ev.extra.get("end_sample", 0)))
    if ev.kind == "speech_start":
        return stt_pb2.TranscribeEvent(
            speech_start=stt_pb2.SpeechStart(at_sample=ev.at_sample))
    if ev.kind == "speech_end":
        return stt_pb2.TranscribeEvent(speech_end=stt_pb2.SpeechEnd(
            at_sample=ev.at_sample, silence_ms=ev.silence_ms))
    return stt_pb2.TranscribeEvent(metrics=stt_pb2.Metrics(
        infer_ms=int(ev.infer_ms), active_sessions=active))


async def serve(args):
    log.info("loading %s …", args.model_path)
    t0 = time.perf_counter()
    engine = SharedEngine(
        args.model_path, args.cif, language=args.language,
        segment_length=args.segment_length, buffer_len=args.buffer_len,
    )
    log.info("loaded in %.1fs on %s", time.perf_counter() - t0, engine.device)
    log.info("warm-up %.0f ms", engine.warm_up())

    servicer = SpeechToTextServicer(engine, args.max_concurrent)
    server = grpc.aio.server(options=[
        # Long-lived streams over a real network die on default keepalive.
        ("grpc.keepalive_time_ms", 20000),
        ("grpc.keepalive_timeout_ms", 10000),
        ("grpc.keepalive_permit_without_calls", 1),
        ("grpc.max_receive_message_length", 4 * 1024 * 1024),
    ])
    stt_pb2_grpc.add_SpeechToTextServicer_to_server(servicer, server)

    # Standard health service so k8s can probe gRPC natively (1.24+).
    # aio variant: its set() is a coroutine. The sync HealthServicer
    # returns None and cannot be awaited.
    health_servicer = health_aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    server.add_insecure_port(f"{args.host}:{args.port}")
    await server.start()

    # Only NOW is the pod ready: model loaded and kernels compiled.
    await health_servicer.set(SERVICE_NAME, health_pb2.HealthCheckResponse.SERVING)
    await health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    log.info("serving on %s:%d (max %d concurrent calls)",
             args.host, args.port, args.max_concurrent)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("draining…")
    await health_servicer.set(SERVICE_NAME, health_pb2.HealthCheckResponse.NOT_SERVING)
    await servicer.drain()
    await server.stop(grace=5)
    log.info("stopped")

    # Force the process down. gRPC's pollers and the CUDA runtime leave non-daemon
    # threads that never join, so a plain return leaves the process alive holding
    # ~1.8 GiB of VRAM until something SIGKILLs it. In k8s that means the pod hangs
    # for its whole termination grace period with the GPU still reserved.
    # The drain and server.stop() above have already completed, so there is nothing
    # left to unwind cleanly.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=50051)
    ap.add_argument("--model-path", default=str(Path.home() / ".cache/whisper/small.pt"))
    ap.add_argument("--cif", default=None)
    ap.add_argument("--language", default="en",
                    help="fallback when a stream's StreamConfig omits one; "
                         "per-stream language is honoured and costs no extra VRAM")
    ap.add_argument("--segment-length", type=float, default=0.5)
    ap.add_argument("--buffer-len", type=float, default=20.0)
    ap.add_argument("--max-concurrent", type=int, default=2,
                    help="MEASURED on a 4GB RTX 3050 with small@0.5s: 2 calls keep up "
                         "(11.2s wall for 10.6s audio), 3 fall 3.3s behind. Re-measure "
                         "for other cards/models rather than trusting this default.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
