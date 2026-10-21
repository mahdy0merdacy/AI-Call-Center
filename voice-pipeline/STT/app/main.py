from fastapi import FastAPI, WebSocket
from .speech_service import SpeechService
from dotenv import load_dotenv
import asyncio

load_dotenv()

app = FastAPI()
speech_service = SpeechService()

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker container health monitoring."""
    return {"status": "healthy", "service": "stt"}

@app.websocket("/stt")
async def stt_socket(ws: WebSocket):
    await ws.accept()
    print("[STT] Client connected")
    
    loop = asyncio.get_running_loop()
    text_queue = asyncio.Queue()

    recognizer, stream = speech_service.create_recognizer()

    def recognized(evt):
        if evt.result.text:
            print(f"[STT] 🎯 Azure recognized: '{evt.result.text}'")
            # ✅ Use call_soon_threadsafe instead
            loop.call_soon_threadsafe(text_queue.put_nowait, evt.result.text)

    recognizer.recognized.connect(recognized)
    recognizer.start_continuous_recognition()

    async def receive_audio():
        try:
            while True:
                audio_chunk = await ws.receive_bytes()
                print(f"[STT] 📥 Received {len(audio_chunk)} bytes")  # ✅ add this
                stream.write(audio_chunk)
        except Exception as e:
            print(f"[STT] Audio receive stopped: {e}")

    async def send_transcripts():
        try:
            while True:
                text = await text_queue.get()
                print(f"[STT] 📤 Sending transcript: '{text}'")
                await ws.send_text(text)
        except Exception as e:
            print(f"[STT] Transcript send stopped: {e}")

    receive_task = asyncio.create_task(receive_audio())
    send_task = asyncio.create_task(send_transcripts())

    try:
        await asyncio.gather(receive_task, send_task)
    except Exception as e:
        print(f"[STT] ❌ Connection closed: {e}")
    finally:
        receive_task.cancel()
        send_task.cancel()
        stream.close()
        recognizer.stop_continuous_recognition()
        print("[STT] ✅ Cleaned up")