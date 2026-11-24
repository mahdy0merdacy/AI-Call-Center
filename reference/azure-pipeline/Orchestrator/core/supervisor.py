import asyncio
import logging
from core.actor import SessionActor
from clients.stt_client import STTClient
from clients.llm_client import LLMClient
from clients.tts_client import TTSClient
from core.settings import settings
from core.redis_client import RedisClient

logger = logging.getLogger(__name__)


class SessionSupervisor:
    """
    Master orchestrator for all active sessions on this pod.
    
    Responsibilities:
    1. Spawn actors (one per client WebSocket connection)
    2. Track active sessions (in memory + Redis)
    3. Terminate sessions on client disconnect
    4. Connect to backend services (STT, LLM, TTS, Redis)
    
    Architecture:
      supervisor._actors = {session_id → SessionActor}
      
      When client connects:
        → spawn(session_id) creates actor + service clients
        → actor.start() launches _run() and _stt_loop() tasks
      
      When client disconnects:
        → terminate(session_id) cancels tasks + closes connections
    
    Scaling consideration (Phase 1):
      This supervisor manages actors on ONE pod only.
      With Redis, we can recover actors on different pods after crash
      (implement session affinity in K8s Ingress later).
    """

    def __init__(self):
        """Initialize supervisor with empty actor registry."""
        self._actors = {}
        self.redis_client: RedisClient = None
        logger.info("[SUPERVISOR] Initialized (awaiting Redis connection)")

    async def connect_redis(self):
        """
        Connect to Redis on startup (called from app.py lifespan).
        
        This is separate from __init__ because:
        1. We need async/await (lifespan startup is async)
        2. Redis connection happens once at boot
        3. If Redis is down, we fail fast with clear error
        """
        self.redis_client = RedisClient(settings.redis_url)
        await self.redis_client.connect()
        logger.info("[SUPERVISOR] ✅ Redis connected")

    async def spawn(self, session_id: str) -> SessionActor:
        """
        Create a new session actor for incoming client.
        
        Steps:
        1. Create STT/LLM/TTS service clients
        2. Connect all service clients (establish WebSockets)
        3. Create SessionActor with these clients + Redis client
        4. Start actor tasks (_run + _stt_loop)
        5. Store in registry and Redis metadata
        
        Args:
            session_id: Unique session identifier (UUID)
        
        Returns:
            SessionActor instance (ready to receive messages)
        
        Example:
            actor = await supervisor.spawn("abc-123-def")
            # Now actor is running and listening for client messages
        """
        logger.info(f"[SUPERVISOR] 🎯 Spawning actor for session {session_id}")

        # Step 1-2: Create and connect service clients
        stt = STTClient(settings.stt_url)
        llm = LLMClient(settings.llm_url)
        tts = TTSClient(settings.tts_url)

        try:
            await stt.connect()
            await llm.connect()
            await tts.connect()
            logger.debug(f"[SUPERVISOR] ✅ Service clients connected for {session_id}")
        except Exception as e:
            logger.error(f"[SUPERVISOR] ❌ Failed to connect services: {e}")
            raise

        # Step 3-4: Create and start actor
        # Pass Redis client so actor can externalize state
        actor = SessionActor(session_id, stt, llm, tts, self.redis_client)
        await actor.start()

        # Step 5: Register in local registry
        self._actors[session_id] = actor

        # Step 6: Set session metadata in Redis
        import socket
        import datetime
        pod_name = socket.gethostname()  # K8s pod name
        await self.redis_client.set_session_metadata(
            session_id,
            {
                "pod_id": pod_name,
                "created_at": datetime.datetime.utcnow().isoformat(),
                "status": "active"
            }
        )

        logger.info(f"[SUPERVISOR] ✅ Actor spawned for {session_id}")
        return actor

    async def get(self, session_id: str) -> SessionActor | None:
        """
        Look up actor by session ID (local lookup only, no Redis).
        
        Args:
            session_id: Unique session identifier
        
        Returns:
            SessionActor if active on this pod, None otherwise
        
        Note:
          This only finds actors on the CURRENT pod.
          If client reconnects to different pod, they won't find the actor here.
          (Session affinity in Ingress prevents this scenario.)
        """
        return self._actors.get(session_id)

    async def terminate(self, session_id: str):
        """
        Gracefully shut down session actor.
        
        Steps:
        1. Remove from local registry
        2. Cancel actor tasks (_run + _stt_loop)
        3. Close service client WebSocket connections
        4. Clear session data from Redis
        
        Args:
            session_id: Unique session identifier
        
        Note:
          Called when:
          - Client WebSocket disconnects
          - Session timeout occurs (not implemented yet)
          - Pod shutdown (lifespan context manager)
        """
        actor = self._actors.pop(session_id, None)
        if not actor:
            logger.warning(f"[SUPERVISOR] Session {session_id} not found in registry")
            return

        logger.info(f"[SUPERVISOR] 🔌 Terminating actor for {session_id}")

        try:
            # Step 2: Cancel actor tasks
            await actor.stop()

            # Step 3: Close service connections
            await actor.stt.close()
            await actor.llm.close()
            await actor.tts.close()

            # Step 4: Clear Redis data
            await self.redis_client.clear_session(session_id)

            logger.info(f"[SUPERVISOR] ✅ Actor terminated for {session_id}")
        except Exception as e:
            logger.error(f"[SUPERVISOR] Error terminating actor: {e}")

    @property
    def active_sessions(self) -> int:
        """
        Get count of active sessions on this pod.
        
        Useful for:
        - Monitoring (gauge metric)
        - Health checks
        - Capacity planning (warn when approaching limit)
        """
        return len(self._actors)

    async def shutdown(self):
        """
        Graceful shutdown: terminate all sessions + close Redis.
        
        Called from app.py lifespan on SIGTERM or graceful stop.
        
        Process:
        1. Mark all sessions for termination
        2. Close actors
        3. Close Redis connection
        """
        logger.info(f"[SUPERVISOR] 🛑 Shutting down with {len(self._actors)} active sessions")

        # Terminate all actors in parallel
        tasks = [self.terminate(sid) for sid in list(self._actors.keys())]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Close Redis connection
        if self.redis_client:
            await self.redis_client.close()

        logger.info("[SUPERVISOR] ✅ Shutdown complete")

    