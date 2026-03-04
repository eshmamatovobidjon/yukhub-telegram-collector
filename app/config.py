from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram ---
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str
    TELEGRAM_PHONE: str
    TELEGRAM_SESSION_NAME: str = "sessions/yukhub_session"

    # --- Database ---
    DATABASE_URL: str  # postgresql+asyncpg://user:pass@host/db

    # --- LLM (OpenAI-compatible interface) ---
    OPENAI_API_KEY: str
    LLM_MODEL: str = "gpt-4o-mini"
    # Override to point at any OpenAI-compatible endpoint:
    # e.g. http://localhost:11434/v1  (Ollama)
    #      http://localhost:8080/v1   (vLLM / llama.cpp server)
    LLM_BASE_URL: Optional[str] = None

    # --- Data retention ---
    MAX_POST_AGE_DAYS: int = 15
    CLEANUP_INTERVAL_HOURS: int = 6

    # --- Queue ---
    QUEUE_MAX_SIZE: int = 5000

    # --- Workers ---
    # Number of concurrent asyncio parser tasks (each does one LLM call at a time)
    PARSER_WORKERS: int = 3

    # --- Logging ---
    LOG_LEVEL: str = "INFO"


settings = Settings()
