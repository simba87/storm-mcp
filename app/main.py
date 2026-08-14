"""
STORM API — FastAPI приложение.

REST-обёртка над Stanford STORM для длительных исследований через HTTP.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import jobs
from .config import settings
from .models import (
    FileContent,
    JobListOut,
    JobOut,
    JobStatus,
    StormRequest,
)

# ──────────────────────────── logging ────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("storm-api")


# ────────────────────── auth (X-API-Key) ─────────────────────
def _check_api_key(request: Request) -> bool:
    """Опциональная авторизация: если STORM_API_KEY задан — требуем X-API-Key.

    STORM_API_KEY пуст → auth выключен (локальная разработка / Docker).
    """
    if not settings.API_KEY:
        return True
    return request.headers.get("X-API-Key", "") == settings.API_KEY


async def require_api_key(request: Request):
    if not _check_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


# ────────────────────── lifespan ──────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("STORM API starting")
    log.info("  LLM provider : %s", settings.LLM_PROVIDER)
    log.info("  Model        : %s", settings.MODEL_NAME)
    log.info("  Search engine: %s", settings.SEARCH_ENGINE)
    log.info("  Max workers  : %d", settings.MAX_WORKERS)
    log.info("  Output dir   : %s", settings.OUTPUT_BASE)
    log.info("  Workdir      : %s", settings.WORKDIR_BASE)
    log.info("  Auth         : %s", "X-API-Key" if settings.API_KEY else "disabled (no STORM_API_KEY)")
    yield
    # Graceful shutdown: SIGTERM активным runner-ам, ждём до 10 сек
    await jobs.shutdown_jobs()
    log.info("STORM API stopped")


# ──────────────────────────── app ────────────────────────────
app = FastAPI(
    title="STORM API",
    version="1.1.0",
    description=(
        "REST-обёртка над [Stanford STORM](https://github.com/stanford-oval/storm). "
        "Запускайте длительные исследования через HTTP, получайте статьи "
        "в формате Wikipedia. Каждая задача асинхронна — результат доступен "
        "по `/jobs/{id}/result`."
    ),
    lifespan=lifespan,
)

# CORS: origins из env (comma-separated). "*" → все origins (только локальная разработка!)
if settings.CORS_ORIGINS.strip() == "*":
    _cors_origins = ["*"]
else:
    _cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────── helpers ───────────────────────────
def _job_or_404(job_id: str) -> dict:
    d = jobs.get_job(job_id)
    if not d:
        raise HTTPException(404, detail=f"Job '{job_id}' not found")
    return d


def _to_job_out(d: dict) -> JobOut:
    return JobOut(
        id=d["id"],
        topic=d["topic"],
        status=d["status"],
        created_at=d["created_at"],
        started_at=d.get("started_at"),
        finished_at=d.get("finished_at"),
        pid=d.get("pid"),
        return_code=d.get("return_code"),
        error=d.get("error"),
        result_topic_dir=d.get("result_topic_dir"),
        files=d.get("files"),
    )


# ──────────────────────────── routes ──────────────────────────
@app.get("/health", tags=["system"])
async def health():
    """Healthcheck для Docker (без auth — для probes)."""
    return {"status": "ok", "jobs_total": len(jobs.list_jobs(1000))}


@app.get("/config", tags=["system"])
async def get_config():
    """Текущая конфигурация (для WebUI). Без секретов."""
    return {
        "llm_provider": settings.LLM_PROVIDER,
        "model_name": settings.MODEL_NAME,
        "temperature": settings.TEMPERATURE,
        "max_tokens": settings.MAX_TOKENS,
        "search_engine": settings.SEARCH_ENGINE,
        "search_top_k": settings.SEARCH_TOP_K,
        "max_workers": settings.MAX_WORKERS,
        "job_timeout": settings.JOB_TIMEOUT_S,
        "do_polish": settings.DO_POLISH,
        "auth_enabled": bool(settings.API_KEY),
    }


# ─── WebUI ───
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/ui", response_class=FileResponse, include_in_schema=False)
async def webui():
    """WebUI для ручного управления задачами."""
    return FileResponse(str(_static_dir / "index.html"))


@app.get("/", tags=["system"])
async def root():
    return {
        "service": "storm-api",
        "version": "1.1.0",
        "docs": "/docs",
        "webui": "/ui",
        "endpoints": {
            "POST   /research":          "Запустить новое исследование",
            "GET    /jobs":              "Список задач",
            "GET    /jobs/{id}":         "Статус задачи",
            "GET    /jobs/{id}/log":     "Лог выполнения",
            "GET    /jobs/{id}/result":  "Список файлов результата",
            "GET    /jobs/{id}/files/{path}": "Скачать файл результата",
            "POST   /jobs/{id}/cancel":  "Отменить задачу",
            "DELETE /jobs/{id}":         "Удалить задачу и файлы",
        },
    }


# ──── создать исследование ────
@app.post(
    "/research",
    response_model=JobOut,
    status_code=201,
    tags=["research"],
    dependencies=[Depends(require_api_key)],
)
async def create_research(req: StormRequest):
    """
    Запускает STORM-исследование. Возвращает `job_id` — используйте его
    для поллинга статуса через `GET /jobs/{id}`.

    Минимальный запрос:
    ```json
    {"topic": "History of quantum computing"}
    ```

    Конфигурация LLM и поиска берётся из переменных окружения контейнера,
    но может быть переопределена в каждом запросе.

    `callback_url` получает POST при переходе задачи в любой терминальный
    статус (done/failed/cancelled). Разрешены только публичные http(s) URL —
    private/loopback адреса отклоняются (SSRF protection).
    """
    try:
        job_id = await jobs.create_job(req)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    d = jobs.get_job(job_id)
    return _to_job_out(d)


# ──── получить статус ────
@app.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    tags=["jobs"],
    dependencies=[Depends(require_api_key)],
)
async def get_job(job_id: str):
    d = _job_or_404(job_id)
    return _to_job_out(d)


# ──── список задач ────
@app.get(
    "/jobs",
    response_model=list[JobListOut],
    tags=["jobs"],
    dependencies=[Depends(require_api_key)],
)
async def list_jobs(limit: int = 50):
    """Возвращает список задач (новые первыми)."""
    items = jobs.list_jobs(limit)
    return [
        JobListOut(
            id=d["id"],
            topic=d["topic"],
            status=d["status"],
            created_at=d["created_at"],
            finished_at=d.get("finished_at"),
        )
        for d in items
    ]


# ──── лог выполнения ────
@app.get(
    "/jobs/{job_id}/log",
    response_class=PlainTextResponse,
    tags=["jobs"],
    dependencies=[Depends(require_api_key)],
)
async def get_log(job_id: str):
    _job_or_404(job_id)
    text = jobs.get_log(job_id)
    if text is None:
        return Response(
            content="# Лог ещё не создан (задача в очереди)",
            media_type="text/plain",
        )
    return text


# ──── список файлов результата ────
@app.get(
    "/jobs/{job_id}/result",
    tags=["research"],
    dependencies=[Depends(require_api_key)],
)
async def get_result(job_id: str):
    d = _job_or_404(job_id)
    if d["status"] != JobStatus.DONE:
        raise HTTPException(
            409,
            detail=f"Job status is '{d['status'].value}', not 'done'. "
                   f"Check /jobs/{job_id}/log for details.",
        )
    files = d.get("files") or {}
    return {
        "job_id": job_id,
        "topic": d["topic"],
        "status": d["status"].value,
        "result_topic_dir": d.get("result_topic_dir"),
        "files": files,
    }


# ──── скачать конкретный файл ────
@app.get(
    "/jobs/{job_id}/files/{file_path:path}",
    tags=["research"],
    dependencies=[Depends(require_api_key)],
)
async def download_file(job_id: str, file_path: str):
    _job_or_404(job_id)
    content = jobs.get_file(job_id, file_path)
    if content is None:
        raise HTTPException(404, detail=f"File '{file_path}' not found")

    # Определяем content-type
    if file_path.endswith(".json"):
        media_type = "application/json"
    elif file_path.endswith(".txt") or file_path.endswith(".md"):
        media_type = "text/plain; charset=utf-8"
    else:
        media_type = "application/octet-stream"

    filename = Path(file_path).name
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ──── отменить задачу ────
@app.post(
    "/jobs/{job_id}/cancel",
    response_model=JobOut,
    tags=["jobs"],
    dependencies=[Depends(require_api_key)],
)
async def cancel_job(job_id: str):
    d = _job_or_404(job_id)
    ok = jobs.cancel_job(job_id)
    if not ok:
        raise HTTPException(409, detail=f"Cannot cancel job in status '{d['status']}'")
    return _to_job_out(jobs.get_job(job_id))


# ──── удалить задачу ────
@app.delete(
    "/jobs/{job_id}",
    tags=["jobs"],
    dependencies=[Depends(require_api_key)],
)
async def delete_job(job_id: str):
    _job_or_404(job_id)
    jobs.delete_job(job_id)
    return {"deleted": job_id}
