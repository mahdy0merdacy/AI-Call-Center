"""End-to-end smoke test against a running server.

Streams a WAV in 20 ms chunks (the cadence WebRTC actually delivers, per
spikes/webrtc-audio) with a trailing silence so the endpointer fires, and prints
every event the service emits.

    python app/server.py &
    python tests/smoke_client.py
"""

import asyncio
import sys
import wave
from pathlib import Path

import grpc
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.gen import stt_pb2, stt_pb2_grpc  # noqa: E402

AUDIO = HERE.parents[2] / "spikes" / "simul-whisper" / "espeak_16k1.wav"
CHUNK_MS = 20
CHUNK = 16000 * CHUNK_MS // 1000


async def main():
    with wave.open(str(AUDIO), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    # a second of silence so the endpointer has something to detect
    pcm = np.concatenate([pcm, np.zeros(16000, dtype=np.int16)])
    print(f"streaming {len(pcm)/16000:.1f}s in {CHUNK_MS}ms chunks\n")

    async def outgoing():
        yield stt_pb2.TranscribeRequest(config=stt_pb2.StreamConfig(
            session_id="smoke-1", language="en",
            silence_ms_to_endpoint=900, min_speech_ms=200, vad_threshold=0.5,
            format=stt_pb2.AudioFormat(
                sample_rate=16000, channels=1,
                encoding=stt_pb2.AudioFormat.PCM_S16LE)))
        for i in range(0, len(pcm), CHUNK):
            block = pcm[i:i + CHUNK]
            yield stt_pb2.TranscribeRequest(audio=stt_pb2.AudioChunk(
                pcm=block.tobytes(), start_sample=i))
            await asyncio.sleep(CHUNK_MS / 1000)  # pace it like a real call

    async with grpc.aio.insecure_channel("127.0.0.1:50051") as ch:
        stub = stt_pb2_grpc.SpeechToTextStub(ch)
        finals = []
        async for ev in stub.Transcribe(outgoing()):
            k = ev.WhichOneof("event")
            if k == "ready":
                print(f"  READY   model={ev.ready.model} device={ev.ready.device}")
            elif k == "speech_start":
                print(f"  SPEECH_START at sample {ev.speech_start.at_sample}")
            elif k == "partial":
                print(f"  partial  +{ev.partial.delta!r}")
            elif k == "speech_end":
                print(f"  SPEECH_END after {ev.speech_end.silence_ms}ms silence")
            elif k == "final":
                print(f"  FINAL    {ev.final.text!r}")
                finals.append(ev.final.text)
        print(f"\n{len(finals)} final(s) — this is what the LLM would receive")
        return 0 if finals else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
