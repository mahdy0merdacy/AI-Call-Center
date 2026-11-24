import asyncio
from mic_service import MicService
from datetime import datetime

URL = "ws://127.0.0.1:8000/ws/session"

async def main():
    mic = MicService(URL)
    await mic.connect()
    
    stream = mic.start_stream()
    stream.start()
    
    print("\n🎤 Speak now! Press Ctrl+C when done.\n")
    
    try:
        async for msg in mic.receive():

            # 🔹 Handle audio first
            if isinstance(msg, bytes):
                print(f"[AUDIO] {len(msg)} bytes")
                continue

            # 🔹 From here: msg is str
            if msg == "__TURN_END__":
                print("\n✅ Turn complete\n")

            elif msg.startswith("TRANSCRIPT: "):
                print(f"🎤 {msg[11:]}", end="", flush=True)

            else:
                print(f"🤖 {msg}", end="", flush=True)

    except KeyboardInterrupt:
        print("\n\n[MIC] Stopping...")

    finally:
        stream.stop()
        stream.close()
        
        # Save the recording
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{timestamp}.wav"
        mic.save_recording(filename)
        
        await mic.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[MIC] Exited")