# Spike: WebRTC audio into aiortc

**Question:** what does browser mic audio actually look like by the time it reaches
`track.recv()` on the aiortc side, and what does it cost to get it to 16 kHz mono?

Static page → `getUserMedia({audio:true})` → one POST `/offer` → aiortc receives one
audio track → loop on `recv()` and measure. Nothing downstream.

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# by hand: open http://localhost:8080 and click Start mic
.venv/bin/python server.py

# or reproducibly, headless Chrome with a fake mic (this is how the numbers below were taken)
.venv/bin/python drive_headless.py --frames 500
.venv/bin/python drive_headless.py --frames 500 --dtx    # opus usedtx=1
.venv/bin/python drive_headless.py --frames 500 --stun   # aiortc's default STUN server
```

No TLS: browsers treat `localhost` as a secure origin, so `getUserMedia` works over plain HTTP.

`drive_headless.py` generates `fake-mic.wav` — 3 s tone bursts alternating with 3 s of
digital silence — and feeds it to Chrome's fake capture device. The silence is what makes
the PTS question answerable rather than hypothetical.

Each run writes `out/report-<ts>.json` (plus `out/latest.json`) with the full per-frame data.

## Results

500 frames per run, 6 runs, Chrome 147.0.7727.55 headless → aiortc 1.15.0 / PyAV 17.1.0 / numpy 2.5.3,
Python 3.12.13, Fedora, loopback (127.0.0.1).

### Frame geometry — constant across every frame, every run

| Property | Value |
| --- | --- |
| `frame.sample_rate` | `48000` |
| `frame.samples` | `960` (20.0 ms) |
| `frame.layout.name` | `stereo` (2 ch) |
| `frame.format.name` | `s16`, packed (`is_planar=False`) |
| `to_ndarray()` | `dtype=int16`, `shape=(1, 1920)`, 3840 bytes |
| `frame.time_base` | `1/48000` |

Packed, so the single row is interleaved L,R,L,R — **not** `(channels, samples)`. Channel
0 is `arr[0, 0::2]`.

**The stereo is fake.** The source is a mono mic, but L and R came back bit-identical in
500/500 frames. aiortc's Opus decoder is hardcoded to 2 channels, so mono input is
duplicated. You are carrying 2× the bytes for no information.

### Arrival interval

| Run | mean | stdev | min | p95 | max |
| --- | --- | --- | --- | --- | --- |
| baseline | 19.999 ms | 0.280 ms | 18.94 | 20.51 | 21.10 |
| baseline ×2 | 19.998 ms | 0.331 ms | — | — | — |
| baseline ×3 | 19.999 ms | 0.273 ms | — | — | — |
| DTX | 19.999 ms | 0.359 ms | — | — | — |

Effective rate 50.00 frames/s. Zero stalls (>30 ms) and zero catch-up bursts (<5 ms) in
any run. On loopback the pacing is essentially perfect — **this number will not survive a
real network**, and it is the one figure here that says more about the test rig than about
WebRTC. Treat sub-millisecond jitter as the floor, not the expectation.

> First attempt reported stdev **6.06 ms** with a 145 ms outlier. That was my own
> instrumentation: the first `to_ndarray()` call costs ~142 ms of lazy import, and paying
> it inside the recv loop stalls the loop and shows up as arrival jitter. `warm_up()` in
> `server.py` now pays that cost up front. Anything doing first-touch work inside the
> audio loop will do the same thing to a real pipeline.

### 48k → 16k resample, `av.AudioResampler(format="s16", layout="mono", rate=16000)`

| | mean | stdev | p50 | p95 | p99 | max |
| --- | --- | --- | --- | --- | --- | --- |
| in the live recv loop | 79–96 µs | 24–30 µs | ~77 µs | 130–166 µs | 186–216 µs | 223–307 µs |
| isolated (`bench_resample.py`) | 20.2 µs | — | 16.7 µs | 29.9 µs | — | — |

**~0.4% of the 20 ms frame budget.** Not a concern at any realistic call volume — roughly
250 concurrent streams before resampling alone saturates one core.

The 4× spread between in-loop and isolated is contention: in the live loop the same core is
also doing SRTP, Opus decode and the asyncio loop. **Budget against the ~80–95 µs in-loop
figure**, not the 20 µs microbenchmark.

Two things worth knowing:

- **First call costs ~850 µs** (graph construction), and ~2.2 ms if PyAV itself is cold.
  Build the resampler and push a dummy frame through it before the call connects.
- **The first output frame is short: 304 samples, not 320.** The resampler holds back 16
  samples priming its filter; every subsequent frame is exactly 320. If you assume a fixed
  320 you will either drop or misalign the first 1 ms of every call.

Dropping the duplicate channel first does *not* pay: deinterleave + mono resample is
20.9 µs vs 20.2 µs for the direct stereo→mono resample — the numpy copy and frame
construction cost what the narrower resample saves. **Keep feeding the stereo frame
straight to a `layout="mono"` resampler**; it does the downmix for free.

### `frame.pts`

- **Monotonic: yes.** Starts at 0, increments by exactly 960 every frame, 499/499 deltas.
- **Gaps during silence: none.** Zero deltas differed from 960 in any run.

This was tested properly, not assumed. The fake mic feeds 3 s of true digital silence:
the spike measured **204/500 frames below −60 dBFS, with an unbroken run of 148 frames
= 2960 ms**, and PTS stepped 960 straight through it. Chrome kept sending 50 packets/s
of encoded silence.

Negotiating **Opus DTX (`usedtx=1`) changed nothing** — same 500 frames in 9.98 s, same
zero gaps. Chrome accepted the fmtp in both offer and answer and kept transmitting anyway.

So on this path `pts` is a reliable, continuous media clock. **Do not build on that
assumption for other paths**: a SIP/PSTN gateway, a mobile network with real loss, or a
client that honours DTX will all produce jumps. `pts` is authoritative for *where* audio
belongs on the timeline; frame count is not a safe proxy for elapsed time.

### Signalling: POST `/offer` → first frame

| | gather (setLocalDescription) | → answer sent | → first frame | **total** |
| --- | --- | --- | --- | --- |
| no STUN (×4 runs) | 7.3–8.3 ms | 13.5–16.0 ms | 122–125 ms | **137–141 ms** |
| aiortc default STUN | 5008.6 ms | 5015.3 ms | 121.4 ms | **5136.7 ms** |

**aiortc's out-of-the-box default costs 5 seconds of call setup on this host.**

`RTCPeerConnection()` with no configuration defaults to `stun:stun.l.google.com:19302`
(`rtcicetransport.py:226`). DNS resolves in 4 ms, but the UDP binding request gets no
reply here, and `aioice.Connection.get_component_candidates` waits out a hard-coded
`timeout=5` before returning candidates. The answer cannot be sent until gathering
finishes, so the whole 5 s lands directly on the caller.

Fix — and it should be the default in the real service, not a flag:

```python
RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
```

For a localhost or same-LAN peer, host candidates are all you need. If a real deployment
needs STUN/TURN for remote callers, use a server that actually answers and verify it, or
that 5 s timeout becomes 5 s of dead air on every inbound call.

The remaining ~125 ms from answer to first frame is DTLS handshake plus Chrome's encoder
startup (ICE reaches `completed` at ~43 ms; the rest is DTLS and the first Opus frame).
That is the real floor for this design.

## What this means for the pipeline

1. **Set `iceServers=[]`** (or a STUN server you have verified). 5 s → 140 ms.
2. **Expect `(1, 1920)` int16 interleaved stereo at 48 kHz, 20 ms per frame** — packed,
   with a duplicated channel.
3. **Feed frames straight to one long-lived `AudioResampler`** with `layout="mono"`,
   pre-warmed, and handle the short 304-sample first frame.
4. **Warm every library path before the first frame arrives.** Lazy imports inside the
   recv loop turn into audio glitches, and they are invisible in a microbenchmark.
5. **Trust `pts`, not frame counts**, for timeline position — the continuity seen here is
   a property of this path, not of WebRTC.
6. 20 ms frames mean STT gets 20 ms of new audio at a time. Anything downstream that wants
   larger windows has to buffer, and that buffer is latency added on top of the ~140 ms
   setup plus whatever the network adds.

## Not covered

Sending audio back, multiple concurrent connections, real network loss/jitter/reordering,
reconnection and ICE restart, non-Chrome browsers, and anything behind a NAT.
