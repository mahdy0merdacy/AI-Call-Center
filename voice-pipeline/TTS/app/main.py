from fastapi import FastAPI, WebSocket
from dotenv import load_dotenv
import asyncio
import wave

from .tts_service import TTSService

load_dotenv()

app = FastAPI()
tts_service = TTSService()

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker container health monitoring."""
    return {"status": "healthy", "service": "tts"}

@app.websocket("/tts")
async def tts_socket(ws: WebSocket):
    await ws.accept()
    print("[TTS] 🔌 client connected")

    loop = asyncio.get_running_loop()

    try:
        while True:
            text = await ws.receive_text()
            print(f"[TTS] 📝 received text: {text}")

            audio_queue = asyncio.Queue(maxsize=20)
            audio_buffer = bytearray()

            # ----------------------------
            # Azure callback bridge
            # ----------------------------
            class AudioStreamCallback:
                def __init__(self, queue, loop):
                    self.queue = queue
                    self.loop = loop

                def write(self, data: memoryview):
                    chunk = bytes(data)

                    print(f"[TTS CALLBACK] chunk: {len(chunk)}")

                    asyncio.run_coroutine_threadsafe(
                        self.queue.put(chunk),
                        self.loop
                    )

                    return len(data)

                def close(self):
                    print("[TTS CALLBACK] 🔒 stream closed (debug only)")

            audio_callback = AudioStreamCallback(audio_queue, loop)
            synthesizer = tts_service.create_synthesizer(audio_callback)

            # ----------------------------
            # Run synthesis in background
            # ----------------------------
            synth_task = asyncio.create_task(
                asyncio.to_thread(
                    lambda: synthesizer.speak_text_async(text).get()
                )
            )

            print("[TTS] ▶️ synthesis started")

            # ----------------------------
            # Streaming loop (ROBUST)
            # ----------------------------
            while True:

                # Exit condition: synthesis done AND queue empty
                if synth_task.done() and audio_queue.empty():
                    print("[TTS] ⏹️ synthesis finished (task done + queue empty)")
                    break

                try:
                    chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                audio_buffer.extend(chunk)

                try:
                    await ws.send_bytes(chunk)
                except Exception as e:
                    print(f"[TTS] ⚠️ websocket closed during stream: {e}")
                    break

            # Ensure synthesis completed
            await synth_task

            # ----------------------------
            # Write WAV file (demo)
            # ----------------------------
            if audio_buffer:
                with wave.open(f"tts_out_{int(loop.time())}.wav", "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(audio_buffer)

                print("[TTS] 💾 wrote tts_out.wav")
            else:
                print("[TTS] ⚠️ empty audio buffer, no file written")

    except Exception as e:
        print("[TTS] ❌ error:", e)

    finally:
        print("[TTS] 🔴 client disconnected")