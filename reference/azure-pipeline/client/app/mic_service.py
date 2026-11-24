import asyncio
import sounddevice as sd
import numpy as np
import websockets
import wave
from datetime import datetime

class MicService:
    def __init__(self, url: str):
        self.url = url
        self.ws = None
        self.loop = None
        self.recording = []

    async def connect(self):
        print("[MIC] Connecting to Orchestrator...")
        self.ws = await websockets.connect(self.url)
        self.loop = asyncio.get_running_loop()
        print("[MIC] Connected ✔")

    def start_stream(self):
        print("[MIC] Starting microphone... speak now")

        def callback(indata, frames, time, status):
            if status:
                print("[MIC STATUS]:", status)

            audio_bytes = indata.tobytes()

            # debug preview (VERY useful)
            print(f"[MIC] sending {len(audio_bytes)} bytes | sample: {audio_bytes[:10]}")

            # store for WAV
            self.recording.append(indata.copy())

            # send async safely
            asyncio.run_coroutine_threadsafe(
                self.ws.send(audio_bytes),
                self.loop
            )

        return sd.InputStream(
            samplerate=16000,
            channels=1,
            dtype='int16',
            blocksize=1600,
            callback=callback,
        )

    async def receive(self):
        print("[MIC] listening for transcripts...\n")
        try:
            while True:
                msg = await self.ws.recv()
                yield msg
        except websockets.ConnectionClosed:
            print("[MIC] orchestrator closed ❌")

    def save_recording(self, filename: str):
        if not self.recording:
            print("[MIC] No audio recorded")
            return

        audio_data = np.concatenate(self.recording)

        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(16000)
            wf.writeframes(audio_data.tobytes())

        print(f"[MIC] Saved recording to {filename}")

    async def close(self):
        print("[MIC] closing connection")
        if self.ws:
            await self.ws.close()
        print("[MIC] closed ✔")