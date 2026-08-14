# ══════════════════════════════════════════════════════════════
# Stage 1: STORM + все heavy зависимости (knowledge-storm)
# ══════════════════════════════════════════════════════════════
FROM python:3.11-slim AS storm-base

# Системные зависимости для knowledge-storm
# (tiktoken, sentence-transformers, playwright/chromium для scraping)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Ставим knowledge-storm из PyPI
# Это тянет dspy-ai, litellm, sentence-transformers, beautifulsoup4 и т.д.
RUN pip install --no-cache-dir knowledge-storm==1.1.1

# Опциональные retrievers (duckduckgo — free, без API key)
RUN pip install --no-cache-dir duckduckgo_search

# Playwright для web-scraping (STORM использует для извлечения контента)
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

# ══════════════════════════════════════════════════════════════
# Stage 2: FastAPI приложение поверх STORM
# ══════════════════════════════════════════════════════════════
FROM storm-base AS api

WORKDIR /app

# Ставим только API-зависимости (storm уже есть из stage 1)
RUN pip install --no-cache-dir \
    fastapi==0.115.6 \
    uvicorn[standard]==0.34.0 \
    httpx==0.28.1 \
    pydantic==2.10.4

# Копируем код приложения
COPY app/        /app/app/
COPY scripts/    /app/scripts/

# Хостовый umask может дать 600 — делаем читаемым для всех
RUN chmod -R a+rX /app

# sitecustomize.py (monkey-patch httpx UA) — должен быть на PYTHONPATH
ENV PYTHONPATH="/app/app"

# Директории для данных
RUN mkdir -p /data/output /data/workdir

# Переменные окружения (переопределяются docker-compose)
ENV STORM_OUTPUT_DIR=/data/output \
    STORM_WORKDIR_BASE=/data/workdir \
    STORM_LLM_PROVIDER=openai \
    STORM_MODEL_NAME=openai/gpt-4o \
    STORM_TEMPERATURE=1.0 \
    STORM_MAX_TOKENS=500 \
    STORM_SEARCH_ENGINE=duckduckgo \
    STORM_SEARCH_TOP_K=3 \
    STORM_MAX_WORKERS=2 \
    STORM_JOB_TIMEOUT=1800 \
    STORM_DO_POLISH=true \
    STORM_MAX_CONV_STEPS=3 \
    STORM_API_HOST=0.0.0.0 \
    STORM_API_PORT=8000

# Non-root user (bind mounts на хосте не будут root-owned).
# UID можно переопределить при сборке: --build-arg STORM_UID=$(id -u)
ARG STORM_UID=10001
RUN useradd -r -s /usr/sbin/nologin -u ${STORM_UID} storm \
    && chown -R storm:storm /data
USER storm

EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
