"""Spike: browser mic -> WebRTC -> aiortc, measure what the audio track actually delivers.

Serves the static page and a single POST /offer endpoint. On the resulting peer
connection it takes the first audio track, loops on recv(), and records:

  * frame geometry (sample_rate, samples, dtype/shape, layout, format)
  * arrival interval between consecutive recv() returns
  * cost of a 48k -> 16k resample per frame, via av.AudioResampler
  * pts: monotonic? does the delta ever differ from the frame size (gaps)?
  * signalling latency: POST /offer received -> first frame out of recv(),
    broken down by SDP step so a slow phase is attributable

Nothing downstream. Prints a summary and writes a JSON report once it has
collected --frames frames.

Everything numpy/av touches is warmed up before the loop starts: the first
to_ndarray() call costs ~100 ms of lazy import, and paying it inside the loop
stalls recv() and shows up as fake arrival jitter.
"""

import argparse
import asyncio
import json
import logging
import re
import signal
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import av
import numpy as np
from aiohttp import web
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

HERE = Path(__file__).parent
STATIC = HERE / "static"

SOURCE_RATE = 48000
TARGET_RATE = 16000
TARGET_LAYOUT = "mono"
TARGET_FORMAT = "s16"

# below this RMS (int16 full scale 32767) a frame is silence, ~-60 dBFS
SILENCE_RMS = 30.0

# an interval this far from the mean is a stall or a catch-up burst, not jitter
BURST_MS = 5.0
STALL_MS = 30.0

log = logging.getLogger("spike")


def describe(values):
    """min/mean/stdev/percentiles for a list of numbers, or None if empty."""
    if not values:
        return None
    ordered = sorted(values)

    def pct(p):
        idx = min(len(ordered) - 1, int(round(p / 100 * len(ordered) + 0.5)) - 1)
        return ordered[idx]

    return {
        "n": len(values),
        "min": ordered[0],
        "mean": statistics.fmean(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "max": ordered[-1],
    }


def warm_up():
    """Pay every lazy import/alloc cost before the measured loop starts."""
    frame = av.AudioFrame(format=TARGET_FORMAT, layout="stereo", samples=960)
    frame.sample_rate = SOURCE_RATE
    frame.pts = 0
    scratch = av.AudioResampler(format=TARGET_FORMAT, layout=TARGET_LAYOUT, rate=TARGET_RATE)
    t0 = time.perf_counter()
    for _ in range(50):
        arr = frame.to_ndarray()
        scratch.resample(frame)
        float(np.sqrt(np.mean(np.square(arr.astype(np.float32)))))
    return (time.perf_counter() - t0) * 1000


def enable_dtx(sdp: str) -> str:
    """Add usedtx=1 to the opus fmtp line.

    For the send direction Chrome takes its encoder config from the remote
    description, so the answer we hand back is what actually turns DTX on.
    """
    payloads = re.findall(r"^a=rtpmap:(\d+) opus/", sdp, re.M)
    out = []
    for line in sdp.splitlines():
        m = re.match(r"^a=fmtp:(\d+) (.*)$", line)
        if m and m.group(1) in payloads and "usedtx" not in m.group(2):
            line = f"a=fmtp:{m.group(1)} {m.group(2)};usedtx=1"
        out.append(line)
    return "\r\n".join(out) + "\r\n"


class Measurement:
    """Everything one track run collects."""

    def __init__(self, target_frames):
        self.target_frames = target_frames
        self.done = asyncio.Event()
        self.consumer = None

        self.t_offer = None          # POST /offer entered
        self.t_remote_set = None     # setRemoteDescription returned
        self.t_answer_created = None # createAnswer returned
        self.t_answer = None         # setLocalDescription returned, answer going back
        self.t_first_frame = None    # first recv() returned
        self.t_last_frame = None
        self.ice_states = []         # (state, perf_counter)
        self.warm_up_ms = None
        self.dtx_requested = False
        self.stun_enabled = False

        self.intervals_ms = []       # gap between consecutive recv() returns
        self.resample_us = []        # steady-state resample cost
        self.resample_first_us = None
        self.to_ndarray_us = []

        self.geometry = Counter()    # distinct frame shapes seen
        self.first_frame_detail = None

        self.pts_values = []
        self.pts_deltas = Counter()
        self.pts_gaps = []           # delta != samples
        self.pts_non_monotonic = []
        self.out_samples = Counter()  # samples per resampled frame
        self.silent = []             # per-frame: is this frame below SILENCE_RMS?
        self.identical_channels = 0  # frames where L and R are bit-identical
        self.rms_us = []
        self.frames = 0

    # -- recording -------------------------------------------------------

    def note_ice(self, state):
        self.ice_states.append((state, time.perf_counter()))

    def add_frame(self, frame, arrival, resample_us, ndarray_us, rms_us, rms,
                  identical_channels, arr, out_frames):
        i = self.frames
        self.frames += 1

        if i == 0:
            self.t_first_frame = arrival
            self.resample_first_us = resample_us
            self.first_frame_detail = {
                "sample_rate": frame.sample_rate,
                "samples": frame.samples,
                "layout_name": frame.layout.name,
                "channels": len(frame.layout.channels),
                "format_name": frame.format.name,
                "is_planar": frame.format.is_planar,
                "time_base": str(frame.time_base),
                "ndarray_dtype": str(arr.dtype),
                "ndarray_shape": list(arr.shape),
                "ndarray_nbytes": int(arr.nbytes),
                "duration_ms": 1000 * frame.samples / frame.sample_rate,
            }
        else:
            self.intervals_ms.append((arrival - self.t_last_frame) * 1000)
            self.resample_us.append(resample_us)

        self.t_last_frame = arrival
        self.to_ndarray_us.append(ndarray_us)
        self.rms_us.append(rms_us)
        if identical_channels:
            self.identical_channels += 1
        self.silent.append(bool(rms < SILENCE_RMS))

        self.geometry[
            (frame.sample_rate, frame.samples, frame.layout.name,
             frame.format.name, str(arr.dtype), arr.shape)
        ] += 1

        for out in out_frames:
            self.out_samples[out.samples] += 1

        pts = frame.pts
        self.pts_values.append(pts)
        if i > 0 and pts is not None and self.pts_values[-2] is not None:
            prev = self.pts_values[-2]
            delta = pts - prev
            self.pts_deltas[delta] += 1
            if delta <= 0:
                self.pts_non_monotonic.append({"index": i, "pts": pts, "prev": prev})
            elif delta != frame.samples:
                self.pts_gaps.append({
                    "index": i,
                    "pts": pts,
                    "delta": delta,
                    "expected": frame.samples,
                    "missing_samples": delta - frame.samples,
                    "gap_ms": round(1000 * (delta - frame.samples) / frame.sample_rate, 2),
                    "at_second": round(arrival - self.t_first_frame, 3),
                })

    def silence_runs(self):
        """Lengths of consecutive silent-frame runs, longest first."""
        runs, current = [], 0
        for quiet in self.silent:
            if quiet:
                current += 1
            elif current:
                runs.append(current)
                current = 0
        if current:
            runs.append(current)
        return sorted(runs, reverse=True)

    # -- reporting -------------------------------------------------------

    def report(self):
        wall = (self.t_last_frame - self.t_first_frame) if self.frames > 1 else 0.0
        bursts = [x for x in self.intervals_ms if x < BURST_MS]
        stalls = [x for x in self.intervals_ms if x > STALL_MS]
        steady = [x for x in self.intervals_ms if BURST_MS <= x <= STALL_MS]
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dtx_requested": self.dtx_requested,
            "stun_enabled": self.stun_enabled,
            "warm_up_ms": self.warm_up_ms,
            "frames": self.frames,
            "wall_seconds": round(wall, 3),
            "effective_frames_per_second": round((self.frames - 1) / wall, 3) if wall else None,
            "frame": self.first_frame_detail,
            "geometry_variants": [
                {
                    "sample_rate": k[0], "samples": k[1], "layout_name": k[2],
                    "format_name": k[3], "ndarray_dtype": k[4],
                    "ndarray_shape": list(k[5]), "count": v,
                }
                for k, v in self.geometry.most_common()
            ],
            "arrival_interval_ms": describe(self.intervals_ms),
            "arrival_interval_steady_ms": describe(steady),
            "arrival_outliers": {
                "burst_under_ms": BURST_MS, "burst_count": len(bursts),
                "stall_over_ms": STALL_MS, "stall_count": len(stalls),
                "stalls": sorted(stalls, reverse=True)[:10],
            },
            "arrival_intervals_raw_ms": [round(x, 4) for x in self.intervals_ms],
            "resample_48k_to_16k_us": describe(self.resample_us),
            "resample_first_call_us": self.resample_first_us,
            "to_ndarray_us": describe(self.to_ndarray_us[1:]),
            "to_ndarray_first_call_us": self.to_ndarray_us[0] if self.to_ndarray_us else None,
            "resampled_frame_samples": dict(self.out_samples),
            "silence": {
                "rms_threshold": SILENCE_RMS,
                "silent_frames": sum(self.silent),
                "silent_fraction": round(sum(self.silent) / max(1, self.frames), 4),
                "longest_run_frames": (self.silence_runs() or [0])[0],
                "longest_run_ms": round(
                    (self.silence_runs() or [0])[0] * 1000
                    * (self.first_frame_detail["samples"] / self.first_frame_detail["sample_rate"]), 1
                ) if self.first_frame_detail else None,
                "runs": self.silence_runs()[:10],
            },
            "rms_us": describe(self.rms_us[1:]),
            "identical_channels": {
                "frames": self.identical_channels,
                "of": self.frames,
                "all_duplicated": self.identical_channels == self.frames,
            },
            "pts": {
                "first": self.pts_values[0] if self.pts_values else None,
                "last": self.pts_values[-1] if self.pts_values else None,
                "monotonic": not self.pts_non_monotonic,
                "deltas": {str(k): v for k, v in sorted(self.pts_deltas.items())},
                "gap_count": len(self.pts_gaps),
                "gaps": self.pts_gaps[:20],
                "non_monotonic": self.pts_non_monotonic[:20],
            },
            "signalling": {
                "offer_to_set_remote_ms": self._ms(self.t_offer, self.t_remote_set),
                "set_remote_to_create_answer_ms": self._ms(self.t_remote_set, self.t_answer_created),
                "create_answer_to_set_local_ms": self._ms(self.t_answer_created, self.t_answer),
                "offer_to_answer_ms": self._ms(self.t_offer, self.t_answer),
                "answer_to_first_frame_ms": self._ms(self.t_answer, self.t_first_frame),
                "offer_to_first_frame_ms": self._ms(self.t_offer, self.t_first_frame),
                "ice_states": [
                    {"state": s, "since_offer_ms": self._ms(self.t_offer, t)}
                    for s, t in self.ice_states
                ],
            },
        }

    @staticmethod
    def _ms(a, b):
        if a is None or b is None:
            return None
        return round((b - a) * 1000, 3)


async def consume(track, m: Measurement):
    """Loop on recv() and time everything. This is the whole point of the spike."""
    m.warm_up_ms = warm_up()
    log.info("warm-up done in %.1f ms", m.warm_up_ms)

    resampler = av.AudioResampler(format=TARGET_FORMAT, layout=TARGET_LAYOUT, rate=TARGET_RATE)

    while m.frames < m.target_frames:
        try:
            frame = await track.recv()
        except Exception as exc:  # track ended / connection dropped
            log.warning("recv() stopped after %d frames: %s", m.frames, exc)
            break
        arrival = time.perf_counter()

        t0 = time.perf_counter_ns()
        arr = frame.to_ndarray()
        t1 = time.perf_counter_ns()
        out_frames = resampler.resample(frame)
        t2 = time.perf_counter_ns()
        rms = float(np.sqrt(np.mean(np.square(arr.astype(np.float32)))))
        t3 = time.perf_counter_ns()

        m.add_frame(frame, arrival,
                    resample_us=(t2 - t1) / 1000,
                    ndarray_us=(t1 - t0) / 1000,
                    rms_us=(t3 - t2) / 1000,
                    rms=rms,
                    identical_channels=(
                        arr.shape[0] == 1 and bool(np.array_equal(arr[0, 0::2], arr[0, 1::2]))
                    ),
                    arr=arr, out_frames=out_frames)

        if m.frames == 1:
            d = m.first_frame_detail
            log.info("first frame: %d Hz, %d samples (%.1f ms), layout=%s, format=%s, "
                     "to_ndarray -> %s %s",
                     d["sample_rate"], d["samples"], d["duration_ms"], d["layout_name"],
                     d["format_name"], d["ndarray_dtype"], tuple(d["ndarray_shape"]))
            log.info("offer -> first frame: %.1f ms", m._ms(m.t_offer, m.t_first_frame))
        elif m.frames % 100 == 0:
            log.info("%d/%d frames | interval mean %.2f ms | resample mean %.1f us",
                     m.frames, m.target_frames,
                     statistics.fmean(m.intervals_ms), statistics.fmean(m.resample_us))

    m.done.set()


def print_summary(rep):
    f, p, s = rep["frame"], rep["pts"], rep["signalling"]
    w = print

    w("")
    w("=" * 68)
    w(f"  WebRTC audio spike — {rep['frames']} frames over {rep['wall_seconds']} s"
      + ("  [DTX]" if rep["dtx_requested"] else "")
      + ("  [STUN]" if rep.get("stun_enabled") else "  [no STUN]"))
    w("=" * 68)

    w("\n-- frame geometry " + "-" * 50)
    w(f"  frame.sample_rate    : {f['sample_rate']} Hz")
    w(f"  frame.samples        : {f['samples']}  ({f['duration_ms']:.1f} ms per frame)")
    w(f"  frame.layout.name    : {f['layout_name']}  ({f['channels']} ch)")
    w(f"  frame.format.name    : {f['format_name']}  (planar={f['is_planar']})")
    w(f"  to_ndarray()         : dtype={f['ndarray_dtype']}  shape={tuple(f['ndarray_shape'])}"
      f"  ({f['ndarray_nbytes']} bytes)")
    w(f"  frame.time_base      : {f['time_base']}")
    ic = rep["identical_channels"]
    w(f"  L == R bit-identical : {ic['frames']}/{ic['of']} frames"
      + ("  (mono source, decoder just duplicates)" if ic["all_duplicated"] else ""))
    if len(rep["geometry_variants"]) > 1:
        w(f"  !! {len(rep['geometry_variants'])} distinct geometries seen:")
        for g in rep["geometry_variants"]:
            w(f"     {g['count']:>5}x {g['sample_rate']} Hz {g['samples']} smp "
              f"{g['layout_name']} {g['ndarray_dtype']}{tuple(g['ndarray_shape'])}")
    else:
        w("  (constant across every frame)")

    a, st, o = rep["arrival_interval_ms"], rep["arrival_interval_steady_ms"], rep["arrival_outliers"]
    w("\n-- frame arrival interval " + "-" * 42)
    w(f"  mean {a['mean']:.3f} ms   stdev {a['stdev']:.3f} ms   (n={a['n']})")
    w(f"  min {a['min']:.3f} | p50 {a['p50']:.3f} | p95 {a['p95']:.3f} | "
      f"p99 {a['p99']:.3f} | max {a['max']:.3f} ms")
    w(f"  excluding bursts/stalls: mean {st['mean']:.3f} ms  stdev {st['stdev']:.3f} ms (n={st['n']})")
    w(f"  catch-up bursts (<{o['burst_under_ms']} ms): {o['burst_count']}   "
      f"stalls (>{o['stall_over_ms']} ms): {o['stall_count']}")
    if o["stalls"]:
        w(f"     worst stalls: {', '.join(f'{x:.1f}' for x in o['stalls'][:5])} ms")
    w(f"  effective rate: {rep['effective_frames_per_second']:.2f} frames/s")

    r = rep["resample_48k_to_16k_us"]
    w("\n-- 48k -> 16k resample, av.AudioResampler " + "-" * 26)
    w(f"  mean {r['mean']:.1f} us   stdev {r['stdev']:.1f} us   (n={r['n']}, "
      f"first call {rep['resample_first_call_us']:.1f} us, excluded)")
    w(f"  min {r['min']:.1f} | p50 {r['p50']:.1f} | p95 {r['p95']:.1f} | "
      f"p99 {r['p99']:.1f} | max {r['max']:.1f} us")
    budget = 1000 * f["duration_ms"]
    w(f"  = {100 * r['mean'] / budget:.3f}% of the {f['duration_ms']:.0f} ms frame budget")
    n = rep["to_ndarray_us"]
    w(f"  (to_ndarray() for comparison: mean {n['mean']:.1f} us, p95 {n['p95']:.1f} us)")
    w(f"  resampled frame sizes: {rep['resampled_frame_samples']}")

    sil = rep["silence"]
    w("\n-- silence (proves the pts question was actually exercised) " + "-" * 8)
    w(f"  frames below RMS {sil['rms_threshold']}: {sil['silent_frames']}"
      f" / {rep['frames']} ({100 * sil['silent_fraction']:.1f}%)")
    w(f"  longest unbroken silent run: {sil['longest_run_frames']} frames"
      f" = {sil['longest_run_ms']} ms")
    w(f"  all silent runs (frames): {sil['runs']}")
    w(f"  (per-frame RMS cost: mean {rep['rms_us']['mean']:.1f} us)")

    w("\n-- pts " + "-" * 61)
    w(f"  monotonic              : {p['monotonic']}")
    w(f"  range                  : {p['first']} -> {p['last']}")
    w(f"  deltas seen            : {p['deltas']}")
    w(f"  gaps (delta != samples): {p['gap_count']}"
      + ("" if p["gap_count"] else "   <- none, including across the silent run above"))
    for g in p["gaps"][:10]:
        w(f"     frame #{g['index']} at t+{g['at_second']}s: delta {g['delta']} "
          f"(expected {g['expected']}) = {g['gap_ms']} ms missing")

    w("\n-- signalling " + "-" * 54)
    w(f"  POST /offer -> setRemoteDescription : {s['offer_to_set_remote_ms']} ms")
    w(f"  -> createAnswer                     : {s['set_remote_to_create_answer_ms']} ms")
    w(f"  -> setLocalDescription (ICE gather) : {s['create_answer_to_set_local_ms']} ms")
    w(f"  POST /offer -> answer sent          : {s['offer_to_answer_ms']} ms")
    w(f"  answer -> first frame               : {s['answer_to_first_frame_ms']} ms")
    w(f"  POST /offer -> FIRST FRAME          : {s['offer_to_first_frame_ms']} ms")
    for x in s["ice_states"]:
        w(f"     '{x['state']}' at +{x['since_offer_ms']} ms")
    w("")


async def offer(request):
    m: Measurement = request.app["measurement"]
    if m.t_offer is None:
        m.t_offer = time.perf_counter()

    params = await request.json()
    m.dtx_requested = "usedtx=1" in params["sdp"]
    pc = RTCPeerConnection(configuration=request.app["rtc_config"])
    request.app["pcs"].add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        m.note_ice(f"connection:{pc.connectionState}")
        log.info("connection state -> %s", pc.connectionState)

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        m.note_ice(f"ice:{pc.iceConnectionState}")

    @pc.on("track")
    def on_track(track):
        log.info("track received: kind=%s id=%s", track.kind, track.id)
        if track.kind == "audio" and m.consumer is None:
            m.consumer = asyncio.ensure_future(consume(track, m))

        @track.on("ended")
        async def on_ended():
            log.info("track ended")

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    m.t_remote_set = time.perf_counter()
    answer_sdp = await pc.createAnswer()
    m.t_answer_created = time.perf_counter()
    await pc.setLocalDescription(answer_sdp)
    m.t_answer = time.perf_counter()

    answer = pc.localDescription.sdp
    if m.dtx_requested:
        answer = enable_dtx(answer)
        log.info("DTX requested by offer — usedtx=1 echoed in the answer")

    return web.json_response({"sdp": answer, "type": pc.localDescription.type})


async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def on_shutdown(app):
    await asyncio.gather(*[pc.close() for pc in app["pcs"]], return_exceptions=True)
    app["pcs"].clear()


async def watch_completion(app, out_dir, exit_when_done):
    m: Measurement = app["measurement"]
    await m.done.wait()
    if m.frames < 2:
        log.error("only %d frame(s) collected — nothing to report", m.frames)
    else:
        rep = m.report()
        print_summary(rep)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(rep, indent=2))
        (out_dir / "latest.json").write_text(json.dumps(rep, indent=2))
        log.info("report written to %s", path)
    if exit_when_done:
        await asyncio.sleep(0.2)
        # SIGINT to ourselves: lets aiohttp unwind cleanly instead of raising
        # GracefulExit out of a task nobody awaits
        signal.raise_signal(signal.SIGINT)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--frames", type=int, default=500, help="frames to measure before reporting")
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--stun", action="store_true",
                    help="use aiortc's default STUN server. Off by default: for a localhost or "
                         "LAN peer it adds nothing, and if the binding request gets no reply "
                         "aioice still burns its full 5 s gathering timeout before answering.")
    ap.add_argument("--exit-when-done", action="store_true",
                    help="shut the server down once the report is written")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aioice").setLevel(logging.WARNING)

    app = web.Application()
    app["pcs"] = set()
    app["rtc_config"] = RTCConfiguration(
        iceServers=[RTCIceServer("stun:stun.l.google.com:19302")] if args.stun else []
    )
    app["measurement"] = Measurement(args.frames)
    app["measurement"].stun_enabled = args.stun
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_static("/static", STATIC)

    async def start_watcher(app):
        app["watcher"] = asyncio.ensure_future(
            watch_completion(app, args.out, args.exit_when_done)
        )

    app.on_startup.append(start_watcher)

    log.info("ICE servers: %s", "default STUN" if args.stun else "none (host candidates only)")
    log.info("measuring %d frames — open http://%s:%d", args.frames, args.host, args.port)
    web.run_app(app, host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
