from fastapi import FastAPI, WebSocket
from .llm_service import LLMService
from dotenv import load_dotenv
load_dotenv()
app = FastAPI()
llm_service = LLMService()

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker container health monitoring."""
    return {"status": "healthy", "service": "llm"}

@app.websocket("/llm")
async def llm_socket(ws: WebSocket):
    await ws.accept()
    print("LLM connected")

    try:
        while True:
            prompt = await ws.receive_text()
            print("\n[LLM PROMPT]:", prompt)
            print("[LLM RESPONSE]: ", end="", flush=True)

            # stream response for THIS prompt only
            for token in llm_service.stream_completion(prompt):
                    await ws.send_text(token)
                    print(token,end="",flush="true")

            # IMPORTANT: mark end of response
            
            await ws.send_text("<END>")
    except Exception as e:
        print("LLM closed:", e)