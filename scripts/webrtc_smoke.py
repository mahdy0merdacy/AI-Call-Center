#!/usr/bin/env python3
"""A real WebRTC call against the containerised core stack, without a browser.

    .venv/bin/python scripts/webrtc_smoke.py                    # SwiftNet: line check via MCP
    .venv/bin/python scripts/webrtc_smoke.py --tenant acme      # Acme: asks for a shipment ID
    .venv/bin/python scripts/webrtc_smoke.py --tenant <slug> --say "How long is the warranty?"

It does what the test page does: POST /offer to the edge with the tenant slug, a
microphone track, and the "events" data channel. The microphone is speech that the
TTS container synthesised (asked from inside the edge container, so that leg is a
container-to-container call too), followed by silence, paced in real time.

What a pass proves, hop by hop:
  host -> edge          WebRTC (ICE over the compose bridge, Opus audio, data channel)
  edge -> stt           gRPC stream: the caller's audio is transcribed
  edge -> orchestrator  gRPC stream: transcript in, directives out
  orchestrator -> mcp   the tool call (SwiftNet: check_line_status)
  edge -> tts           the agent's reply is synthesised and played back

Exit code 0 only if the call connected, the greeting played, every utterance was
transcribed and answered with audio, and (Acme default script) the tool ran.
"""

from __future__ import annotations

import argparse
import asyncio
import fractions
import io
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import aiohttp
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack
from av import AudioFrame

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "compose.yaml")]
RATE = 48000
FRAME = 960                      # 20 ms at 48 kHz, what Opus wants

# Default conversation per test tenant: (utterances, tool the last one must trigger).
# SwiftNet's slot is digits, which STT gets right; Acme's ID is spelled letters,
# which Whisper often hears as words ("A, C" -> "I see"), so its script stops at
# the slot prompt and asserts no tool.
SCRIPTS = {
    "swiftnet": (["My internet is down.",
                  "Zero one four two, eight eight seven, seven one one."], "check_line_status"),
    "acme": (["Where is my parcel?"], None),
}

SYNTH = r'''
import json, sys, urllib.request
body = json.dumps({"model": "magpie", "input": sys.argv[1], "voice": "Jason",
                   "response_format": "wav", "sample_rate": 22050}).encode()
req = urllib.request.Request("http://tts:8083/v1/audio/speech", data=body,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as r:
    sys.stdout.buffer.write(r.read())
'''


def synthesize(text: str) -> np.ndarray:
    """Speech for `text` as 48 kHz mono int16, synthesised by the tts container."""
    p = subprocess.run([*COMPOSE, "exec", "-T", "edge", "python", "-", text],
                       input=SYNTH.encode(), capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"TTS via edge failed: {p.stderr.decode()[-300:]}")
    with wave.open(io.BytesIO(p.stdout)) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    n = int(len(pcm) * RATE / rate)
    return np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)),
                     pcm.astype(np.float32)).astype(np.int16)


def wav_seconds(blob: bytes) -> float:
    try:
        with wave.open(io.BytesIO(blob)) as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError):
        return 0.0


class Microphone(AudioStreamTrack):
    """Plays queued utterances, silence otherwise, in real time like a sound card."""

    def __init__(self):
        super().__init__()
        self.pending: list[np.ndarray] = []
        self.buf = np.zeros(0, dtype=np.int16)
        self.t0: float | None = None
        self.sent = 0
        self.talking = asyncio.Event()

    def say(self, pcm: np.ndarray):
        self.pending.append(pcm)

    async def recv(self) -> AudioFrame:
        if self.t0 is None:
            self.t0 = time.monotonic()
        due = self.t0 + self.sent / RATE
        delay = due - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        if len(self.buf) < FRAME and self.pending:
            self.buf = np.concatenate([self.buf, self.pending.pop(0)])
            self.talking.set()
        if len(self.buf) >= FRAME:
            chunk, self.buf = self.buf[:FRAME], self.buf[FRAME:]
        else:
            chunk = np.zeros(FRAME, dtype=np.int16)
            self.talking.clear()
        frame = AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        frame.pts = self.sent
        frame.time_base = fractions.Fraction(1, RATE)
        self.sent += FRAME
        return frame


async def run(args) -> int:
    script, expect_tool = SCRIPTS.get(args.tenant, (["Hello, what can you do?"], None))
    if args.say:
        script, expect_tool = args.say, args.expect_tool
    print(f"synthesising {len(script)} utterance(s) on the tts container…", flush=True)
    utterances = [synthesize(t) for t in script]

    mic = Microphone()
    pc = RTCPeerConnection()
    pc.addTrack(mic)
    chan = pc.createDataChannel("events")

    t_start = time.monotonic()
    log: list[dict] = []
    agent_audio_until = [0.0]             # monotonic time the agent's speech ends
    audio_secs: list[float] = []          # agent audio actually received
    finals: list[str] = []
    speaking: list[str] = []
    tools: list[str] = []
    errors: list[str] = []
    got_tenant = asyncio.Event()
    pc_connected = [False]
    new_speech = asyncio.Event()

    def stamp() -> str:
        return f"{time.monotonic() - t_start:6.1f}s"

    @chan.on("message")
    def on_message(msg):
        if isinstance(msg, (bytes, bytearray)):
            secs = wav_seconds(bytes(msg))
            audio_secs.append(secs)
            agent_audio_until[0] = max(agent_audio_until[0], time.monotonic() + secs)
            print(f"{stamp()}  <- agent audio {secs:.1f}s", flush=True)
            new_speech.set()
            return
        m = json.loads(msg)
        log.append(m)
        t = m.get("type")
        if t == "tenant":
            print(f"{stamp()}  tenant {m.get('name')} ({m.get('locale')}, voice {m.get('voice')})")
            got_tenant.set()
        elif t == "final":
            finals.append(m.get("text", ""))
            print(f"{stamp()}  STT final: {m.get('text')!r}")
        elif t == "speaking":
            speaking.append(m.get("text", ""))
            # The WAV follows on the same channel; until it lands, assume ~15
            # characters a second so a lost audio message cannot stall the script.
            est = len(m.get("text", "")) / 15
            agent_audio_until[0] = max(agent_audio_until[0], time.monotonic() + est)
            new_speech.set()
            print(f"{stamp()}  agent says: {m.get('text')!r}")
        elif t == "tool_result":
            tools.append(f"{m.get('tool')}->{m.get('outcome')}")
            print(f"{stamp()}  TOOL {m.get('tool')} -> {m.get('outcome')}")
        elif t == "error":
            errors.append(f"{m.get('code')}: {m.get('detail')}")
            print(f"{stamp()}  ERROR {m.get('code')}: {m.get('detail')}")

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"{stamp()}  connection {pc.connectionState}", flush=True)
        if pc.connectionState == "connected":
            pc_connected[0] = True

    await pc.setLocalDescription(await pc.createOffer())
    async with aiohttp.ClientSession() as http:
        async with http.post(f"{args.edge}/offer", json={
                "sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
                "tenant": args.tenant}, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                print(f"/offer HTTP {r.status}: {(await r.text())[:200]}")
                return 1
            ans = await r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))

    async def quiet(timeout: float) -> bool:
        """Wait until the agent has finished speaking (and stayed quiet a moment)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if new_speech.is_set() and time.monotonic() > agent_audio_until[0] + 1.2:
                return True
            await asyncio.sleep(0.1)
        return False

    async def reached_orchestrator():
        # The tenant message, or failing that the greeting: either proves the call
        # got through. "tenant resolved" is still checked on its own below.
        while not (got_tenant.is_set() or new_speech.is_set()):
            await asyncio.sleep(0.05)

    ok = True
    try:
        await asyncio.wait_for(reached_orchestrator(), 20)
        if not await quiet(30):
            print("no greeting audio")
            ok = False
        for text, pcm in zip(script, utterances, strict=True):
            if not ok:
                break
            new_speech.clear()
            n_finals = len(finals)
            print(f"{stamp()}  -> caller says: {text!r} ({len(pcm) / RATE:.1f}s)", flush=True)
            mic.say(pcm)
            if not await quiet(args.turn_timeout):
                print(f"no spoken reply to {text!r}")
                ok = False
            elif len(finals) == n_finals:
                print(f"no transcript for {text!r}")
                ok = False
    except TimeoutError:
        print("neither a tenant message nor a greeting: the call never reached the orchestrator")
        ok = False
    finally:
        await pc.close()

    checks = [
        ("WebRTC connected", pc_connected[0]),
        ("tenant message reached the page", got_tenant.is_set()),
        ("greeting played", bool(speaking)),
        ("every utterance transcribed", len([f for f in finals if f]) >= len(script)),
        ("agent replied (text)", len(speaking) >= 1 + len(script)),
        ("agent audio received over WebRTC", sum(audio_secs) > 1.0),
        ("no errors on the data channel", not errors),
    ]
    if expect_tool:
        checks.append((f"MCP tool ran ({expect_tool})",
                       any(t.startswith(expect_tool) for t in tools)))
    print()
    for name, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    return 0 if ok and all(p for _, p in checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--edge", default="http://localhost:8082")
    ap.add_argument("--tenant", default="swiftnet")
    ap.add_argument("--expect-tool", help="with --say: the tool the call must run")
    ap.add_argument("--say", action="append", help="an utterance (repeatable)")
    ap.add_argument("--turn-timeout", type=float, default=45)
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
