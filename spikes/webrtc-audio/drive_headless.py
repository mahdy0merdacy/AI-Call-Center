"""Run the spike end to end with no hands: server + headless Chrome feeding a fake mic.

Chrome's fake capture device plays a WAV file as if it were the microphone, so the
measurement goes through a real browser audio stack (WebAudio capture, Opus encode,
SRTP) and is reproducible run to run. The generated WAV alternates tone bursts and
silence, which is what exercises the "do pts gaps appear during silence?" question.

    python drive_headless.py --frames 500

Everything a real user would do by hand — open localhost:8080, click Start mic —
Chrome does via ?autostart=1.
"""

import argparse
import array
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

HERE = Path(__file__).parent

CHROME_CANDIDATES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]


def make_wav(path: Path, seconds=30, rate=48000, burst=3.0, gap=3.0):
    """Speech-ish tone bursts separated by digital silence."""
    samples = array.array("h")
    period = burst + gap
    for n in range(int(seconds * rate)):
        t = n / rate
        if (t % period) < burst:
            # a couple of harmonics with a slow envelope — not a pure tone, so the
            # encoder has something to chew on
            env = 0.35 * (0.6 + 0.4 * math.sin(2 * math.pi * 2.0 * t))
            v = (math.sin(2 * math.pi * 220 * t)
                 + 0.5 * math.sin(2 * math.pi * 440 * t)
                 + 0.25 * math.sin(2 * math.pi * 880 * t)) / 1.75
            samples.append(int(max(-1.0, min(1.0, env * v)) * 32767))
        else:
            samples.append(0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return path


def find_chrome():
    for name in CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    sys.exit(f"no chrome found (looked for {', '.join(CHROME_CANDIDATES)})")


def wait_for_port(host, port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.15)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=500)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--audio", type=Path, help="WAV to feed the fake mic (default: generated)")
    ap.add_argument("--burst", type=float, default=3.0, help="tone seconds in the generated WAV")
    ap.add_argument("--gap", type=float, default=3.0, help="silence seconds in the generated WAV")
    ap.add_argument("--stun", action="store_true",
                    help="let the server use aiortc's default STUN server")
    ap.add_argument("--dtx", action="store_true",
                    help="negotiate opus usedtx=1, so the sender goes quiet during silence")
    ap.add_argument("--headful", action="store_true", help="show the browser window")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    audio = args.audio
    if audio is None:
        audio = HERE / "fake-mic.wav"
        seconds = max(30, int(args.frames * 0.02 * 1.5))
        make_wav(audio, seconds=seconds, burst=args.burst, gap=args.gap)
        print(f"[drive] generated {audio} ({seconds}s, {args.burst}s tone / {args.gap}s silence)")

    chrome = find_chrome()
    print(f"[drive] chrome: {chrome}")

    server = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"),
         "--host", args.host, "--port", str(args.port),
         "--frames", str(args.frames), "--exit-when-done"]
        + (["--stun"] if args.stun else []),
        stdout=None, stderr=None,
    )
    if not wait_for_port(args.host, args.port):
        server.kill()
        sys.exit("[drive] server never came up")
    print(f"[drive] server up on http://{args.host}:{args.port}")

    profile = tempfile.mkdtemp(prefix="spike-chrome-")
    flags = [
        chrome,
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={audio}",
        "--autoplay-policy=no-user-gesture-required",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--disable-dev-shm-usage",
        f"http://{args.host}:{args.port}/?autostart=1" + ("&dtx=1" if args.dtx else ""),
    ]
    if not args.headful:
        flags.insert(1, "--headless=new")

    browser = subprocess.Popen(
        flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print("[drive] browser launched, waiting for the report…")

    try:
        server.wait(timeout=args.timeout)
        rc = server.returncode
    except subprocess.TimeoutExpired:
        print(f"[drive] TIMEOUT after {args.timeout}s — killing server")
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        rc = 1
    finally:
        try:
            os.killpg(os.getpgid(browser.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            browser.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print(f"[drive] done (server rc={rc})")
    return 0 if rc in (0, None) else rc


if __name__ == "__main__":
    sys.exit(main())
