import websockets
from typing import AsyncGenerator

class TTSClient:
    """
    Protocol:
    - Send: complete text string
    - Receive: audio chunks (bytes) until stream ends
    """
    def __init__(self, url: str):
        self.url = url
        self._ws = None

    async def connect(self):
        self._ws = await websockets.connect(self.url)
        print("[TTS CLIENT] 🔌 Connected")

    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        Send text, stream back audio chunks.
        Returns when TTS service stops sending chunks (1 second timeout).
        """
        print(f"[TTS CLIENT] 📤 Sending text: '{text[:50]}...'")
        await self._ws.send(text)

        # TTS service streams audio chunks back
        while True:
            try:
                import asyncio
                chunk = await asyncio.wait_for(self._ws.recv(), timeout=2.0)
                print(f"[TTS CLIENT] 🎵 Received {len(chunk)} bytes")
                yield chunk
            except asyncio.TimeoutError:
                print("[TTS CLIENT] ⏹️ Stream complete (timeout)")
                break
            except Exception as e:
                print(f"[TTS CLIENT] ❌ Error: {e}")
                break

    async def close(self):
        if self._ws:
            await self._ws.close()
            print("[TTS CLIENT] 🔴 Disconnected")