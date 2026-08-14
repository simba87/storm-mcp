"""
Конфигурация через переменные окружения.
Все значения можно переопределить в docker-compose.yml или .env
"""
from __future__ import annotations

import os
from pathlib import Path


class Settings:
    # ── paths ──
    OUTPUT_BASE: Path  = Path(os.getenv("STORM_OUTPUT_DIR",   "/data/output"))
    WORKDIR_BASE: Path = Path(os.getenv("STORM_WORKDIR_BASE", "/data/workdir"))

    # ── LLM provider: openai | claude | ollama | litellm ──
    LLM_PROVIDER: str  = os.getenv("STORM_LLM_PROVIDER", "openai")

    # ── model names (litellm-совместимые: openai/gpt-4o, anthropic/claude-3-5-sonnet, etc.) ──
    MODEL_NAME: str         = os.getenv("STORM_MODEL_NAME", "openai/gpt-4o")
    EMBEDDING_MODEL: str    = os.getenv("STORM_EMBEDDING_MODEL", "openai/text-embedding-3-small")
    TEMPERATURE: float      = float(os.getenv("STORM_TEMPERATURE", "1.0"))
    MAX_TOKENS: int         = int(os.getenv("STORM_MAX_TOKENS", "500"))
    MAX_CONV_STEPS: int     = int(os.getenv("STORM_MAX_CONV_STEPS", "3"))

    # ── search ──
    # you | bing | brave | serper | duckduckgo | tavily | searxng
    SEARCH_ENGINE: str = os.getenv("STORM_SEARCH_ENGINE", "you")
    SEARCH_TOP_K: int  = int(os.getenv("STORM_SEARCH_TOP_K", "3"))
    SEARXNG_URL: str   = os.getenv("STORM_SEARXNG_URL", "")

    # ── runner ──
    DO_POLISH: bool       = os.getenv("STORM_DO_POLISH", "true").lower() == "true"
    MAX_WORKERS: int      = int(os.getenv("STORM_MAX_WORKERS", "2"))  # параллельных задач
    JOB_TIMEOUT_S: int    = int(os.getenv("STORM_JOB_TIMEOUT", "1800"))  # 30 min

    # ── API server ──
    API_HOST: str = os.getenv("STORM_API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("STORM_API_PORT", "8000"))

    # ── security ──
    # Если задан — все endpoints (кроме /health и /ui) требуют header X-API-Key.
    # Пустой → auth выключен (локальная разработка / закрытый Docker network).
    API_KEY: str = os.getenv("STORM_API_KEY", "")
    # CORS origins, comma-separated. "*" → все (только для локальной разработки!).
    CORS_ORIGINS: str = os.getenv("STORM_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")

    def __init__(self):
        self.OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        self.WORKDIR_BASE.mkdir(parents=True, exist_ok=True)


settings = Settings()
