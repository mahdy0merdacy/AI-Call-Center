import asyncio
import websockets
import wave


TTS_URL="ws://127.0.0.1:8003/tts"


async def main():
    print("attempting connection")
    async with websockets.connect(TTS_URL) as ws:
        print("client connected")
        
        text = input("prompt placeholder")
        await ws.send(text)


        print ("awaiting TTS reply")


        audio_chunks=[]


        try:
            while True:
                chunk=await asyncio.wait_for(ws.recv(), timeout=5.0)

                if isinstance(chunk, bytes):
                    print(f"client recieved {len(chunk)} bytes")
                    audio_chunks.append(chunk)
        except asyncio.TimeoutError:
            print("tts timeout")

    
        if audio_chunks:
            audio_data=b"".join(audio_chunks)

            filename="tts_output.wav"
            with wave.open(filename,"wb") as wf:
                wf.setnchannels(1)      # mono
                wf.setsampwidth(2)      # 16-bit
                wf.setframerate(16000)  # adjust if needed
                wf.writeframes(audio_data)
            print(f"[CLIENT] Saved audio to {filename}")
        else:
            print("[CLIENT] ❌ No audio received")


if __name__ == "__main__":
    asyncio.run(main())