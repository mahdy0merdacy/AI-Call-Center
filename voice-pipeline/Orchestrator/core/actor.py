import asyncio
import logging
from core.mailbox import Mailbox, RedisMailbox
from clients.stt_client import STTClient
from clients.llm_client import LLMClient
from clients.tts_client import TTSClient
from core.redis_client import RedisClient

logger = logging.getLogger(__name__)


class SessionActor:
    """
    Per-session actor managing one user's conversation.
    
    Responsibilities:
    1. Manage session state (conversation history, current transcript)
    2. Coordinate STT → LLM → TTS pipeline
    3. Handle mailbox (messages from client)
    4. Stream outputs to client via output_queue
    5. Externalize state to Redis (new in Phase 1)
    
    Architecture:
      Input path:
        client WebSocket → mailbox → actor.send()
      
      Processing:
        _run() main loop processes mailbox messages
        _stt_loop() background task receives transcripts
      
      Output path:
        actor.recv() → output_queue → client WebSocket
    
    State Management (with Redis):
      - Conversation history: self.history (loaded from Redis at start)
        + Append to Redis on each message
      - Mailbox: RedisMailbox (if Redis available) or AsyncIOQueue (fallback)
      - Full transcript: self.full_transcript (in-memory, ephemeral)
    
    Lifecycle:
      1. __init__: Initialize state, set up clients
      2. start(): Launch background tasks
      3. [Running]: Receive messages, process, send outputs
      4. stop(): Cancel tasks, cleanup
    """

    def __init__(
        self,
        session_id: str,
        stt: STTClient,
        llm: LLMClient,
        tts: TTSClient,
        redis_client: RedisClient = None,
    ):
        """
        Initialize session actor.
        
        Args:
            session_id: Unique session identifier (UUID)
            stt: STTClient instance (connected)
            llm: LLMClient instance (connected)
            tts: TTSClient instance (connected)
            redis_client: RedisClient for state externalization (optional)
        """
        self.session_id = session_id
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.redis_client = redis_client

        # ────────────────────────────────────────────────────────────────
        # STATE: Conversation History
        # ────────────────────────────────────────────────────────────────
        # With Redis: Each message is also persisted to Redis history:{session_id}
        # Enables recovery if pod crashes mid-conversation
        self.history: list[dict] = []

        # ────────────────────────────────────────────────────────────────
        # TRANSPORT: Message Queues
        # ────────────────────────────────────────────────────────────────
        # mailbox: Messages FROM client (audio, turn_end, stop)
        #   - RedisMailbox if Redis available
        #   - AsyncIOQueue otherwise (backwards compatible)
        # output_queue: Messages TO client (transcripts, tokens, audio)
        #   - Always AsyncIOQueue (tied to active WebSocket connection)
        if redis_client:
            self.mailbox = RedisMailbox(redis_client, session_id)
            logger.info(f"[ACTOR] {session_id}: Using Redis mailbox")
        else:
            self.mailbox = Mailbox()
            logger.info(f"[ACTOR] {session_id}: Using in-process mailbox")

        self.output_queue: asyncio.Queue = asyncio.Queue()

        # ────────────────────────────────────────────────────────────────
        # TASKS: Background Coroutines
        # ────────────────────────────────────────────────────────────────
        self._task: asyncio.Task | None = None  # Main loop: _run()
        self._stt_task: asyncio.Task | None = None  # Background: _stt_loop()

        # ────────────────────────────────────────────────────────────────
        # EPHEMERAL: Current Turn State
        # ────────────────────────────────────────────────────────────────
        # full_transcript: Accumulates STT text until turn ends
        #   - Reset after each _handle_turn_end()
        #   - Not persisted (temporary per-turn state)
        self.full_transcript = ""

        logger.info(f"[ACTOR] {self.session_id}: Initialized")

    async def start(self):
        """
        Launch background tasks for this session.
        
        Starts:
        1. _stt_loop(): Continuously receives transcripts from STT
        2. _run(): Main message loop processing mailbox
        
        Called once when actor is spawned.
        """
        self._stt_task = asyncio.create_task(self._stt_loop())
        self._task = asyncio.create_task(self._run())
        logger.debug(f"[ACTOR] {self.session_id}: Tasks started")

    async def send(self, msg: dict):
        """
        Receive message from client WebSocket (via orchestrator).
        
        Message types:
        - {"type": "audio", "data": bytes}: Audio chunk for STT
        - {"type": "turn_end"}: User finished speaking
        - {"type": "stop"}: Client disconnected
        
        Args:
            msg: Message dict from client
        """
        await self.mailbox.enqueue(msg)

    async def recv(self) -> str | bytes | dict:
        """
        Send message to client WebSocket (via orchestrator).
        
        Called by orchestrator's send_loop() to get next output.
        Blocks until message available.
        
        Returns:
            Message to send to client (transcript, token, audio, etc.)
        """
        return await self.output_queue.get()

    # ════════════════════════════════════════════════════════════════════
    # INTERNAL: Main Loop
    # ════════════════════════════════════════════════════════════════════

    async def _run(self):
        """
        Main message loop: process client messages from mailbox.
        
        Runs continuously until "stop" message.
        
        Message handling:
        - "audio": Forward to STT
        - "turn_end": Process accumulated transcript with LLM
        - "stop": Cleanup and exit
        """
        logger.debug(f"[ACTOR] {self.session_id}: Main loop started")

        while True:
            try:
                msg = await self.mailbox.dequeue()

                if msg["type"] == "heartbeat":
                    # Mailbox timeout; ignore and continue
                    continue

                if msg["type"] == "audio":
                    await self._handle_audio(msg["data"])

                elif msg["type"] == "turn_end":
                    await self._handle_turn_end()

                elif msg["type"] == "stop":
                    await self._handle_close()
                    break

            except Exception as e:
                logger.error(f"[ACTOR] {self.session_id}: Error in main loop: {e}")
                break

        logger.debug(f"[ACTOR] {self.session_id}: Main loop exited")

    async def _stt_loop(self):
        """
        Background task: continuously poll STT for transcripts.
        
        Flow:
        1. Receive transcript from STT
        2. Accumulate in self.full_transcript
        3. Append to conversation history (local + Redis)
        4. Launch _handle_turn_end() in background (non-blocking)
        5. Send transcript to client for live display
        6. Loop back to step 1
        
        Design note:
          - Non-blocking: _handle_turn_end spawns as new task
          - STT loop continues receiving transcripts immediately
          - Multiple turn endings can overlap (safe with prompt snapshots)
        """
        logger.debug(f"[ACTOR] {self.session_id}: STT loop started")

        while True:
            try:
                # Receive final transcript from STT (Azure recognized event)
                transcript = await self.stt.recv_text()

                if transcript:
                    logger.info(
                        f"[ACTOR] {self.session_id}: Received transcript: '{transcript}'"
                    )

                    # Step 1-2: Accumulate
                    self.full_transcript = transcript.strip()

                    # Step 3: Append to history (local + Redis)
                    message = {"role": "user", "content": transcript}
                    self.history.append(message)
                    if self.redis_client:
                        await self.redis_client.append_to_history(
                            self.session_id, message
                        )

                    # Step 4: Send to client for live display
                    await self.output_queue.put({"type": "transcript", "data": transcript})

                    # Step 5: Trigger LLM processing (non-blocking)
                    asyncio.create_task(
                        self._handle_turn_end(self.full_transcript)
                    )

            except Exception as e:
                logger.error(f"[ACTOR] {self.session_id}: STT loop error: {e}")
                break

        logger.debug(f"[ACTOR] {self.session_id}: STT loop exited")

    # ════════════════════════════════════════════════════════════════════
    # INTERNAL: Turn Processing (STT → LLM → TTS)
    # ════════════════════════════════════════════════════════════════════

    async def _handle_turn_end(self, prompt: str | None = None):
        """
        Process end of user's utterance: call LLM and stream response.
        
        Steps:
        1. Get user prompt (snapshot passed or use accumulated transcript)
        2. Call LLM with assembled context
        3. Stream LLM tokens to client
        4. Synthesize response with TTS
        5. Stream audio chunks to client
        6. Append assistant response to history
        7. Signal turn complete
        
        Args:
            prompt: Snapshot of user's prompt (prevents race if multiple turns overlap)
        
        Design note:
          - Accepts optional prompt parameter for snapshot isolation
          - Multiple calls can run concurrently (safe)
          - Each call handles its own LLM token streaming + TTS synthesis
        """
        # Step 1: Get prompt (use parameter if provided, otherwise accumulated)
        if prompt is None:
            prompt = self.full_transcript.strip()

        if not prompt:
            logger.warning(f"[ACTOR] {self.session_id}: Turn ended with no transcript")
            return

        logger.info(f"[ACTOR] {self.session_id}: Processing turn: '{prompt[:50]}...'")

        try:
            # Step 2-3: Call LLM and stream tokens
            full_response = ""
            logger.debug(f"[ACTOR] {self.session_id}: Sending to LLM...")

            async for token in self.llm.generate(prompt):
                # Stream token to client immediately
                full_response += token
                await self.output_queue.put({"type": "token", "data": token})

            logger.info(
                f"[ACTOR] {self.session_id}: LLM complete: '{full_response[:50]}...'"
            )

            # Step 4-5: Synthesize and stream audio
            if full_response.strip():
                logger.debug(f"[ACTOR] {self.session_id}: Sending to TTS...")

                async for audio_chunk in self.tts.synthesize(full_response):
                    # Stream audio chunk to client
                    await self.output_queue.put(
                        {"type": "audio", "data": audio_chunk}
                    )

                logger.info(f"[ACTOR] {self.session_id}: TTS complete")

            # Step 6: Append to history
            assistant_message = {"role": "assistant", "content": full_response}
            self.history.append(assistant_message)
            if self.redis_client:
                await self.redis_client.append_to_history(
                    self.session_id, assistant_message
                )

            # Step 7: Signal turn complete
            await self.output_queue.put({"type": "turn_complete"})

        except Exception as e:
            logger.error(f"[ACTOR] {self.session_id}: Error processing turn: {e}")
            await self.output_queue.put({"type": "error", "data": str(e)})

        finally:
            # Reset ephemeral state if we consumed the internal buffer
            if prompt == self.full_transcript.strip():
                self.full_transcript = ""

    async def _handle_audio(self, audio: bytes):
        """
        Forward audio chunk to STT service.
        
        Args:
            audio: Audio bytes (typically 3200 bytes per chunk)
        """
        await self.stt.send_audio(audio)

    async def _handle_close(self):
        """
        Handle session close: cleanup and final turn.
        
        Steps:
        1. Cancel STT background loop
        2. If pending transcript, process it (last turn)
        3. Signal session end
        4. Cleanup Redis state (supervisor will do this too)
        """
        logger.info(f"[ACTOR] {self.session_id}: Closing session")

        # Step 1: Cancel STT loop
        if self._stt_task:
            self._stt_task.cancel()
            try:
                await self._stt_task
            except asyncio.CancelledError:
                logger.debug(f"[ACTOR] {self.session_id}: STT loop cancelled")

        # Step 2: Process final pending transcript if exists
        if self.full_transcript.strip():
            logger.info(
                f"[ACTOR] {self.session_id}: Processing final transcript: '{self.full_transcript}'"
            )
            await self._handle_turn_end(self.full_transcript)

        # Step 3: Signal end
        await self.output_queue.put({"type": "session_end"})

        logger.info(f"[ACTOR] {self.session_id}: Session closed")

    async def stop(self):
        """
        Cancel main task (called by supervisor on terminate).
        
        Gracefully stops the _run() loop.
        """
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug(f"[ACTOR] {self.session_id}: Main task cancelled")

            
            

            
