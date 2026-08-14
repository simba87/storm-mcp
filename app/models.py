"""
Pydantic-модели REST API.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─────────────────────────── enums ──────────────────────────
class JobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class LLMProvider(str, Enum):
    OPENAI  = "openai"
    CLAUDE  = "claude"
    OLLAMA  = "ollama"
    LITELLM = "litellm"


# ────────────────────────── request ─────────────────────────
class StormRequest(BaseModel):
    """Запрос на генерацию энциклопедической статьи по теме."""
    topic: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Тема исследования (например: 'History of quantum computing')",
        examples=["History of quantum computing"],
    )

    # Переопределение LLM (если не задано — берётся из config/env)
    llm_provider: Optional[LLMProvider] = None
    model_name: Optional[str] = Field(
        None,
        description="Litellm-формат: openai/gpt-4o, anthropic/claude-3-5-sonnet, etc.",
    )
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=100, le=32000)

    # Переопределение поиска
    search_engine: Optional[str] = Field(
        None,
        description="you | bing | brave | serper | duckduckgo | tavily | searxng",
    )
    search_top_k: Optional[int] = Field(None, ge=1, le=20)

    # Опции раннера
    do_polish: Optional[bool] = None
    max_conv_steps: Optional[int] = Field(None, ge=1, le=10)

    # Webhook
    callback_url: Optional[str] = Field(
        None,
        description="POST-запрос с результатом при завершении задачи",
    )


# ────────────────────────── response ────────────────────────
class JobOut(BaseModel):
    id: str
    topic: str
    status: JobStatus
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    pid: Optional[int] = None
    return_code: Optional[int] = None
    log_tail: Optional[str] = None
    error: Optional[str] = None
    result_topic_dir: Optional[str] = None  # имя поддиректории с результатом
    files: Optional[Dict[str, str]] = None  # имя_файла → URL


class JobListOut(BaseModel):
    id: str
    topic: str
    status: JobStatus
    created_at: float
    finished_at: Optional[float] = None


class FileContent(BaseModel):
    job_id: str
    filename: str
    size: int
    content: str


class ResultListing(BaseModel):
    job_id: str
    topic: str
    status: JobStatus
    files: Dict[str, str]  # filename → download URL
