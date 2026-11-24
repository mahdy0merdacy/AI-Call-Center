import uuid
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from core.supervisor import SessionSupervisor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global supervisor instance
supervisor = SessionSupervisor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for startup/shutdown.
    
    Startup:
      1. Connect to Redis (Phase 1 externalization)
      2. Ready to accept WebSocket connections
    
    Shutdown:
      1. Gracefully terminate all active sessions
      2. Close Redis connection
    
    This runs ONCE per orchestrator pod lifetime.
    """
    logger.info("[ORCHESTRATOR] 🚀 Starting up...")

    try:
        # Connect to Redis on startup
        await supervisor.connect_redis()
        logger.info("[ORCHESTRATOR] ✅ Redis connected, ready to accept connections")
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] ❌ Failed to connect Redis: {e}")
        logger.warning("[ORCHESTRATOR] ⚠️  Falling back to in-memory mailbox (limited scaling)")
        # Could raise here for strict requirements, or continue with graceful degradation
        # For now, continue anyway (in-process queues will work)

    yield  # Application runs here

    logger.info("[ORCHESTRATOR] 🛑 Shutting down...")
    await supervisor.shutdown()
    logger.info("[ORCHESTRATOR] ✅ Shutdown complete")


# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health_check():
    """
    Health check endpoint for Docker container health monitoring.
    Returns 200 OK if orchestrator is running (doesn't check Redis connectivity).
    """
    return {"status": "healthy"}


@app.websocket("/ws/session")
async def session_endpoint(ws: WebSocket):
    """
    WebSocket endpoint for client connections.
    
    Flow:
    1. Accept WebSocket from client
    2. Generate unique session ID
    3. Spawn SessionActor for this session
    4. Run two concurrent tasks:
       - receive_loop: Get audio chunks from client, put in actor mailbox
       - send_loop: Get outputs from actor, send to client
    5. On disconnect, terminate actor
    
    This endpoint handles ONE client connection.
    Multiple clients = multiple concurrent WebSocket connections = multiple actors.
    
    Data flow:
      Client (audio)
        └─ ws.receive_bytes()
           └─ receive_loop()
              └─ actor.send({"type":"audio", ...})
                 └─ mailbox.enqueue()
                    └─ _run() processes
                       └─ _handle_audio() → STT
    
      Client (output)
        ← ws.send_text() / ws.send_bytes()
        ← send_loop()
           ← actor.recv()
              ← output_queue.get()
                 ← _stt_loop() / _handle_turn_end()
    """
    await ws.accept()
    session_id = str(uuid.uuid4())

    logger.info(f"[ORCHESTRATOR] 🎯 New connection: {session_id}")

    try:
        # Spawn actor for this session
        actor = await supervisor.spawn(session_id)
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Failed to spawn actor: {e}")
        await ws.close(code=1011, reason="Failed to initialize session")
        return

    # Define receive and send tasks

    async def receive_loop():
        """
        Continuously receive audio chunks from client WebSocket.
        Put each chunk into actor mailbox.
        
        On client disconnect, signal actor to close.
        """
        try:
            while True:
                audio_chunk = await ws.receive_bytes()
                logger.debug(
                    f"[ORCHESTRATOR] {session_id}: Received {len(audio_chunk)} bytes from client"
                )
                # Put audio into actor mailbox (will be processed by _run() loop)
                await actor.send({"type": "audio", "data": audio_chunk})

        except WebSocketDisconnect:
            logger.info(f"[ORCHESTRATOR] {session_id}: Client disconnected")
            # Signal actor to gracefully close
            await actor.send({"type": "stop"})

        except Exception as e:
            logger.error(f"[ORCHESTRATOR] {session_id}: Receive loop error: {e}")
            await actor.send({"type": "stop"})

    async def send_loop():
        """
        Continuously receive outputs from actor.
        Send each output to client WebSocket.
        
        Output types:
        - "transcript": STT result (text)
        - "token": LLM token (text, streamed)
        - "audio": TTS chunk (bytes, streamed)
        - "turn_complete": End of turn marker
        - "error": Error message
        """
        try:
            while True:
                msg = await actor.recv()
                logger.debug(
                    f"[ORCHESTRATOR] {session_id}: Sending {msg['type']} to client"
                )

                try:
                    if msg["type"] == "transcript":
                        # Send transcript to client for display
                        await ws.send_text(f"TRANSCRIPT: {msg['data']}")

                    elif msg["type"] == "token":
                        # Stream LLM token in real-time
                        await ws.send_text(msg["data"])

                    elif msg["type"] == "audio":
                        # Stream audio chunk in real-time
                        await ws.send_bytes(msg["data"])

                    elif msg["type"] == "turn_complete":
                        # Signal end of turn
                        await ws.send_text("__TURN_END__")

                    elif msg["type"] == "error":
                        # Send error to client
                        await ws.send_text(f"ERROR: {msg['data']}")

                    elif msg["type"] == "session_end":
                        # Session over, close loop
                        break

                except Exception as e:
                    logger.error(f"[ORCHESTRATOR] {session_id}: Failed to send: {e}")
                    # Continue trying to send other messages

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[ORCHESTRATOR] {session_id}: Send loop error: {e}")

    # Run both loops concurrently
    receive_task = asyncio.create_task(receive_loop())
    send_task = asyncio.create_task(send_loop())

    try:
        # Both tasks run concurrently until one completes
        await asyncio.gather(receive_task, send_task)
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] {session_id}: Gather error: {e}")
    finally:
        # Cleanup: cancel remaining tasks
        receive_task.cancel()
        send_task.cancel()

        # Terminate actor and clean up resources
        logger.info(f"[ORCHESTRATOR] {session_id}: Cleaning up...")
        await supervisor.terminate(session_id)

        logger.info(f"[ORCHESTRATOR] {session_id}: Session completed")



