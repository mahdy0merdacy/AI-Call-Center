import asyncio
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class Mailbox:
    """
    Message queue abstraction for session mailbox.
    
    Purpose:
      Decouples actor message handling from underlying queue implementation.
      This is the ONLY file that changes when migrating queue backends.
    
    Supports two backends:
    1. AsyncIOQueue (default): In-process asyncio.Queue (for dev/testing)
    2. RedisMailbox: Redis Streams (for production/scaling)
    
    The choice is made at initialization time - the actor never knows the difference.
    """

    def __init__(self):
        """Use AsyncIOQueue by default (backwards compatible)."""
        self._impl = AsyncIOQueue()

    async def enqueue(self, msg: dict):
        """Add message to queue."""
        await self._impl.enqueue(msg)

    async def dequeue(self) -> dict:
        """Get next message from queue (blocking)."""
        return await self._impl.dequeue()

    def empty(self) -> bool:
        """Check if queue is empty."""
        return self._impl.empty()


class AsyncIOQueue:
    """
    Simple asyncio.Queue wrapper.
    Used for local development and testing.
    """

    def __init__(self):
        self._q = asyncio.Queue()

    async def enqueue(self, msg: dict):
        await self._q.put(msg)

    async def dequeue(self) -> dict:
        return await self._q.get()

    def empty(self) -> bool:
        return self._q.empty()


class RedisMailbox:
    """
    Redis Streams-based mailbox implementation.
    
    Purpose:
      Messages are persisted in Redis, enabling:
      - Cross-pod message delivery
      - Session recovery after pod crash
      - Horizontal scaling (any pod can read messages)
    
    Implementation:
      Uses Redis STREAM data structure:
      - XADD: Append message
      - XREAD BLOCK: Blocking read with timeout
      - Key: mailbox:{session_id}
    
    API (same as AsyncIOQueue):
      - enqueue(msg): Add message
      - dequeue(): Get message (blocks until available)
      - empty(): Check if has messages
    """

    def __init__(self, redis_client, session_id: str):
        """
        Args:
            redis_client: RedisClient instance (from core/redis_client.py)
            session_id: Unique session identifier
        """
        self.redis_client = redis_client
        self.session_id = session_id
        self.last_id = "0"  # Track position in stream

    async def enqueue(self, msg: dict):
        """
        Add message to Redis stream.
        
        Args:
            msg: Message dict to add
        
        Note:
            Redis XADD is durable - messages survive pod crashes.
        """
        await self.redis_client.enqueue_message(self.session_id, msg)

    async def dequeue(self) -> dict:
        """
        Read next message from Redis stream (blocking with 1s timeout).
        
        Returns:
            Message dict
        
        Behavior:
            - Blocks if no messages available (timeout: 1s)
            - Returns immediately if message is available
            - Creates hole in stream on error (caller should retry)
        """
        # Block 1000ms (1 second) for new message
        result = await self.redis_client.dequeue_message(
            self.session_id,
            last_id=self.last_id,
            timeout=1000
        )

        if result:
            msg_id, message = result
            self.last_id = msg_id  # Update position for next read
            return message
        else:
            # On timeout or error, return sentinel message
            # Actor will handle this gracefully
            return {"type": "heartbeat"}

    async def empty(self) -> bool:
        """Check if mailbox has pending messages."""
        length = await self.redis_client.mailbox_len(self.session_id)
        return length == 0
