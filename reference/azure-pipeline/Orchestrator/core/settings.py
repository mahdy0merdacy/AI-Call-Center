from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Configuration for Orchestrator Service.
    
    All settings can be overridden via environment variables:
    - STT_URL, LLM_URL, TTS_URL: Service URLs (WebSocket endpoints)
    - REDIS_URL: Redis connection string (required for state externalization)
    
    Example .env file:
        STT_URL=ws://stt-service:8001/stt
        LLM_URL=ws://llm-service:8002/llm
        TTS_URL=ws://tts-service:8003/tts
        REDIS_URL=redis://redis-service:6379
    
    For production with authentication:
        REDIS_URL=rediss://user:password@redis.hostname:6380
    """
    stt_url: str = "ws://localhost:8001/stt"
    llm_url: str = "ws://localhost:8002/llm"
    tts_url: str = "ws://localhost:8003/tts"
    # Optional but required for phase 1 (state externalization)
    redis_url: str = "redis://localhost:6379"

settings = Settings()