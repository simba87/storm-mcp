"""
STORM API — FastAPI приложение.

REST-обёртка над Stanford STORM для длительных исследований через HTTP.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
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

# ──────────────────────────── app ────────────────────────────
app = FastAPI(
    title="STORM API",
    version="1.0.0",
    description=(
        "REST-обёртка над [Stanford STORM](https://github.com/stanford-oval/storm). "
        "Запускайте длительные исследования через HTTP, получайте статьи "
        "в формате Wikipedia. Каждая задача асинхронна — результат доступен "
        "по `/jobs/{{id}}/result`."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    """Healthcheck для Docker."""
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
        "version": "1.0.0",
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
@app.post("/research", response_model=JobOut, status_code=201, tags=["research"])
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
    """
    job_id = await jobs.create_job(req)
    d = jobs.get_job(job_id)
    return _to_job_out(d)


# ──── получить статус ────
@app.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
async def get_job(job_id: str):
    d = _job_or_404(job_id)
    return _to_job_out(d)


# ──── список задач ────
@app.get("/jobs", response_model=list[JobListOut], tags=["jobs"])
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
@app.get("/jobs/{job_id}/result", tags=["research"])
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
@app.get("/jobs/{job_id}/files/{file_path:path}", tags=["research"])
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
@app.post("/jobs/{job_id}/cancel", response_model=JobOut, tags=["jobs"])
async def cancel_job(job_id: str):
    d = _job_or_404(job_id)
    ok = jobs.cancel_job(job_id)
    if not ok:
        raise HTTPException(409, detail=f"Cannot cancel job in status '{d['status']}'")
    return _to_job_out(jobs.get_job(job_id))


# ──── удалить задачу ────
@app.delete("/jobs/{job_id}", tags=["jobs"])
async def delete_job(job_id: str):
    _job_or_404(job_id)
    jobs.delete_job(job_id)
    return {"deleted": job_id}


# ──────────────────────────── startup ────────────────────────
@app.on_event("startup")
async def _startup():
    log.info("STORM API starting")
    log.info("  LLM provider : %s", settings.LLM_PROVIDER)
    log.info("  Model        : %s", settings.MODEL_NAME)
    log.info("  Search engine: %s", settings.SEARCH_ENGINE)
    log.info("  Max workers  : %d", settings.MAX_WORKERS)
    log.info("  Output dir   : %s", settings.OUTPUT_BASE)
    log.info("  Workdir      : %s", settings.WORKDIR_BASE)
