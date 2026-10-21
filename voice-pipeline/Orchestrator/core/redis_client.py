"""
Redis Client Wrapper for Session State Externalization

Purpose:
  Provides async Redis operations for:
  - Conversation history storage (Redis LIST)
  - Mailbox queue storage (Redis STREAM)
  - Session metadata (Redis HASH)

Design:
  This is the single source of truth for all Redis interactions.
  If we need to swap Redis for another backend (DynamoDB, Firestore),
  we only modify this file.

Async:
  Uses redis-py with asyncio support (redis[asyncio] package).
  All methods are async and non-blocking.
"""

import redis.asyncio as redis
import json
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class RedisClient:
    """
    Async Redis client wrapper for session state management.
    
    Handles three key data structures:
    1. Conversation History (LIST): history:{session_id}
    2. Mailbox Messages (STREAM): mailbox:{session_id}
    3. Session Metadata (HASH): session:{session_id}
    """

    def __init__(self, redis_url: str):
        """
        Initialize Redis client (connection created lazily on first use).
        
        Args:
            redis_url: Redis connection URL (e.g., "redis://localhost:6379" or 
                      "rediss://user:pass@redis.hostname:6380" for TLS)
        """
        self.redis_url = redis_url
        self._client: Optional[redis.Redis] = None

    async def connect(self):
        """
        Establish connection to Redis.
        Called once on orchestrator startup.
        """
        try:
            self._client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                health_check_interval=30
            )
            # Verify connection
            await self._client.ping()
            logger.info(f"[REDIS] ✅ Connected to {self.redis_url.split('@')[-1]}")
        except Exception as e:
            logger.error(f"[REDIS] ❌ Connection failed: {e}")
            raise

    async def close(self):
        """
        Close Redis connection.
        Called on orchestrator shutdown.
        """
        if self._client:
            await self._client.close()
            logger.info("[REDIS] ✅ Connection closed")

    # ════════════════════════════════════════════════════════════════════
    # CONVERSATION HISTORY (Redis LIST)
    # Key format: history:{session_id}
    # ════════════════════════════════════════════════════════════════════

    async def append_to_history(self, session_id: str, message: Dict[str, str]) -> int:
        """
        Append a message to conversation history.
        
        Args:
            session_id: Unique session identifier
            message: Dict with keys {"role": "user"|"assistant", "content": "..."}
        
        Returns:
            Length of list after append (Redis RPUSH return value)
        
        Example:
            await redis_client.append_to_history(
                "abc-123",
                {"role": "user", "content": "Hello"}
            )
        """
        key = f"history:{session_id}"
        message_json = json.dumps(message)
        try:
            length = await self._client.rpush(key, message_json)
            # Set expiration: 24 hours (session expires after inactivity)
            await self._client.expire(key, 86400)
            logger.debug(f"[REDIS] Appended to {key}, length={length}")
            return length
        except Exception as e:
            logger.error(f"[REDIS] Error appending to history: {e}")
            raise

    async def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """
        Retrieve full conversation history for a session.
        
        Args:
            session_id: Unique session identifier
        
        Returns:
            List of message dicts: [{"role": "...", "content": "..."}, ...]
        
        Example:
            history = await redis_client.get_history("abc-123")
            # Returns: [
            #   {"role": "user", "content": "Hello"},
            #   {"role": "assistant", "content": "Hi there!"}
            # ]
        """
        key = f"history:{session_id}"
        try:
            # LRANGE key 0 -1 gets all elements
            messages_json = await self._client.lrange(key, 0, -1)
            messages = [json.loads(msg) for msg in messages_json]
            logger.debug(f"[REDIS] Retrieved {len(messages)} messages from {key}")
            return messages
        except Exception as e:
            logger.error(f"[REDIS] Error retrieving history: {e}")
            return []

    async def clear_history(self, session_id: str) -> None:
        """
        Delete conversation history for a session (cleanup on session end).
        
        Args:
            session_id: Unique session identifier
        """
        key = f"history:{session_id}"
        try:
            await self._client.delete(key)
            logger.debug(f"[REDIS] Cleared {key}")
        except Exception as e:
            logger.error(f"[REDIS] Error clearing history: {e}")

    # ════════════════════════════════════════════════════════════════════
    # MAILBOX (Redis STREAM)
    # Key format: mailbox:{session_id}
    # 
    # Redis STREAM is ideal for this:
    # - XADD: Append message to stream
    # - XREAD: Read new messages (blocking)
    # - XLEN: Get queue length
    # ════════════════════════════════════════════════════════════════════

    async def enqueue_message(self, session_id: str, message: Dict[str, Any]) -> str:
        """
        Add a message to the mailbox stream.
        
        Args:
            session_id: Unique session identifier
            message: Message dict (usually {"type": "audio", "data": ...})
        
        Returns:
            Message ID (Redis XADD return value, e.g., "1234567890000-0")
        
        Example:
            msg_id = await redis_client.enqueue_message(
                "abc-123",
                {"type": "audio", "data": b"..."}
            )
        """
        key = f"mailbox:{session_id}"
        try:
            # Redis STREAM stores fields as key-value pairs
            # We'll store the entire message as JSON in a single field
            message_json = json.dumps(message, default=str)  # default=str handles bytes
            msg_id = await self._client.xadd(key, {"msg": message_json})
            await self._client.expire(key, 86400)  # 24 hour expiration
            logger.debug(f"[REDIS] Enqueued message {msg_id} to {key}")
            return msg_id
        except Exception as e:
            logger.error(f"[REDIS] Error enqueueing message: {e}")
            raise

    async def dequeue_message(
        self, 
        session_id: str, 
        last_id: str = "0",
        timeout: int = 0
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        """
        Read the next message from mailbox stream (blocking read available).
        
        Args:
            session_id: Unique session identifier
            last_id: Read messages after this ID (use "0" to read from start)
            timeout: Milliseconds to block waiting for new message (0 = no block)
        
        Returns:
            Tuple of (message_id, message_dict) or None if timeout/error
        
        Example:
            # Blocking read: wait up to 1 second for next message
            result = await redis_client.dequeue_message("abc-123", last_id="0", timeout=1000)
            if result:
                msg_id, message = result
                print(f"Received: {message}")
        """
        key = f"mailbox:{session_id}"
        try:
            # XREAD BLOCK {timeout} STREAMS {key} {last_id}
            # Returns: [[key, [[msg_id, {fields}], ...]]] or None on timeout
            result = await self._client.xread(
                {key: last_id},
                block=timeout,
                count=1  # Read one message at a time
            )
            
            if not result or not result[0][1]:
                return None
            
            # Extract message ID and data
            stream_key, messages = result[0]
            msg_id, fields = messages[0]
            message_json = fields.get("msg", "{}")
            message = json.loads(message_json)
            
            logger.debug(f"[REDIS] Dequeued message {msg_id} from {key}")
            return (msg_id, message)
        except redis.exceptions.ResponseError as e:
            # Likely timeout or stream doesn't exist
            return None
        except Exception as e:
            logger.error(f"[REDIS] Error dequeueing message: {e}")
            return None

    async def mailbox_len(self, session_id: str) -> int:
        """
        Get current number of unread messages in mailbox.
        
        Args:
            session_id: Unique session identifier
        
        Returns:
            Length of stream
        """
        key = f"mailbox:{session_id}"
        try:
            length = await self._client.xlen(key)
            return length
        except Exception as e:
            logger.error(f"[REDIS] Error getting mailbox length: {e}")
            return 0

    async def clear_mailbox(self, session_id: str) -> None:
        """
        Delete mailbox stream for a session (cleanup on session end).
        
        Args:
            session_id: Unique session identifier
        """
        key = f"mailbox:{session_id}"
        try:
            await self._client.delete(key)
            logger.debug(f"[REDIS] Cleared {key}")
        except Exception as e:
            logger.error(f"[REDIS] Error clearing mailbox: {e}")

    # ════════════════════════════════════════════════════════════════════
    # SESSION METADATA (Redis HASH)
    # Key format: session:{session_id}
    # ════════════════════════════════════════════════════════════════════

    async def set_session_metadata(
        self, 
        session_id: str, 
        metadata: Dict[str, str]
    ) -> None:
        """
        Store session metadata (pod_id, created_at, etc.).
        
        Args:
            session_id: Unique session identifier
            metadata: Dict of metadata (e.g., {"pod_id": "pod-1", "created_at": "2026-04-26T..."})
        
        Example:
            await redis_client.set_session_metadata(
                "abc-123",
                {
                    "pod_id": "orchestrator-pod-1",
                    "created_at": "2026-04-26T12:30:00Z",
                    "user_ip": "192.168.1.1"
                }
            )
        """
        key = f"session:{session_id}"
        try:
            # HSET stores all fields
            await self._client.hset(key, mapping=metadata)
            await self._client.expire(key, 86400)
            logger.debug(f"[REDIS] Set metadata for {key}")
        except Exception as e:
            logger.error(f"[REDIS] Error setting session metadata: {e}")
            raise

    async def get_session_metadata(self, session_id: str) -> Dict[str, str]:
        """
        Retrieve session metadata.
        
        Args:
            session_id: Unique session identifier
        
        Returns:
            Metadata dict or empty dict if not found
        """
        key = f"session:{session_id}"
        try:
            metadata = await self._client.hgetall(key)
            logger.debug(f"[REDIS] Retrieved metadata from {key}")
            return metadata or {}
        except Exception as e:
            logger.error(f"[REDIS] Error retrieving session metadata: {e}")
            return {}

    async def clear_session(self, session_id: str) -> None:
        """
        Delete all session data (history, mailbox, metadata) - cleanup on disconnection.
        
        Args:
            session_id: Unique session identifier
        """
        try:
            prefix = f"*{session_id}"
            # Get all keys matching pattern (history:*, mailbox:*, session:*)
            keys = await self._client.keys(f"{prefix}")
            if keys:
                await self._client.delete(*keys)
                logger.debug(f"[REDIS] Cleared {len(keys)} keys for session {session_id}")
        except Exception as e:
            logger.error(f"[REDIS] Error clearing session: {e}")

    # ════════════════════════════════════════════════════════════════════
    # UTILITY METHODS
    # ════════════════════════════════════════════════════════════════════

    async def health_check(self) -> bool:
        """
        Simple health check - returns True if Redis is responsive.
        
        Useful for:
        - Orchestrator startup verification
        - Monitoring/alerting
        """
        try:
            await self._client.ping()
            return True
        except Exception as e:
            logger.error(f"[REDIS] Health check failed: {e}")
            return False
