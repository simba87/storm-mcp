"""
In-memory job store с asyncio-диспетчеризацией.
Каждая задача запускается в отдельном subprocess (storm_runner.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .config import settings
from .models import JobStatus, StormRequest

log = logging.getLogger("storm-api.jobs")

# ──────────────────────────── state ──────────────────────────
_jobs: Dict[str, Dict[str, Any]] = {}
_semaphore: Optional[asyncio.Semaphore] = None


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.MAX_WORKERS)
    return _semaphore


# ──────────────────────────── helpers ────────────────────────
def _runner_script() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / "storm_runner.py"


def _build_runner_config(job_id: str, req: StormRequest) -> dict:
    """Маппинг StormRequest → JSON-конфиг для storm_runner.py."""
    work_dir   = str(settings.WORKDIR_BASE / job_id)
    output_dir = str(settings.OUTPUT_BASE / job_id)
    return {
        "topic": req.topic,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "llm_provider": (req.llm_provider.value if req.llm_provider else settings.LLM_PROVIDER),
        "model_name": req.model_name or settings.MODEL_NAME.split("/", 1)[-1],
        "temperature": req.temperature if req.temperature is not None else settings.TEMPERATURE,
        "max_tokens": req.max_tokens or settings.MAX_TOKENS,
        "search_engine": req.search_engine or settings.SEARCH_ENGINE,
        "search_top_k": req.search_top_k or settings.SEARCH_TOP_K,
        "do_polish": req.do_polish if req.do_polish is not None else settings.DO_POLISH,
        "max_conv_steps": req.max_conv_steps or settings.MAX_CONV_STEPS,
        "custom_instructions": req.custom_instructions,
    }


def _read_log_tail(job_id: str, lines: int = 30) -> Optional[str]:
    log_path = settings.WORKDIR_BASE / job_id / "storm.log"
    if not log_path.exists():
        return None
    try:
        data = log_path.read_text(encoding="utf-8", errors="replace")
        all_lines = data.splitlines()
        return "\n".join(all_lines[-lines:])
    except Exception:
        return None


def _scan_result_files(job_id: str) -> Dict[str, str]:
    """Сканирует output-директорию задачи, возвращает {filename: url}."""
    out_dir = settings.OUTPUT_BASE / job_id
    files: Dict[str, str] = {}
    if not out_dir.exists():
        return files
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(out_dir)
            files[str(rel)] = f"/jobs/{job_id}/files/{rel}"
    return files


# ──────────────────────────── core ───────────────────────────
async def create_job(req: StormRequest) -> str:
    """Создаёт задачу, запускает фоновый runner."""
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "id": job_id,
        "topic": req.topic,
        "status": JobStatus.PENDING,
        "created_at": time.time(),
        "log_path": str(settings.WORKDIR_BASE / job_id / "storm.log"),
        "callback_url": req.callback_url,
    }
    asyncio.create_task(_execute(job_id, req))
    return job_id


async def _execute(job_id: str, req: StormRequest):
    """Запускает storm_runner.py в subprocess, дожидается завершения."""
    async with get_semaphore():
        d = _jobs[job_id]
        work_dir   = settings.WORKDIR_BASE / job_id
        output_dir = settings.OUTPUT_BASE / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path   = work_dir / "storm.log"

        cfg = _build_runner_config(job_id, req)
        d["status"]     = JobStatus.RUNNING
        d["started_at"] = time.time()

        # Передаём конфиг через stdin, а не аргументы (безопасно + длина)
        cfg_json = json.dumps(cfg, ensure_ascii=False)

        cmd = [sys.executable, "-u", str(_runner_script())]
        log.info("Job %s starting: %s", job_id, req.topic)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(work_dir),
                env=os.environ.copy(),
            )
            d["pid"] = proc.pid

            # Пишем конфиг в stdin, затем закрываем
            assert proc.stdin
            proc.stdin.write(cfg_json.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            # Читаем stdout → лог-файл (streaming)
            log_file = open(log_path, "w", encoding="utf-8")
            last_line = ""
            try:
                assert proc.stdout
                while True:
                    chunk = await proc.stdout.readline()
                    if not chunk:
                        break
                    line = chunk.decode("utf-8", "replace")
                    log_file.write(line)
                    log_file.flush()
                    last_line = line.strip()
            finally:
                log_file.close()

            rc = await asyncio.wait_for(proc.wait(), timeout=1)
            d["return_code"] = rc
            d["finished_at"] = time.time()

            # Парсим финальный JSON из последней строки runner-вывода
            result_data = {}
            if last_line:
                try:
                    result_data = json.loads(last_line)
                except json.JSONDecodeError:
                    pass

            if rc == 0 and result_data.get("status") == "done":
                d["status"] = JobStatus.DONE
                d["result_topic_dir"] = result_data.get("result_dir", "")
            else:
                d["status"] = JobStatus.FAILED
                d["error"] = result_data.get("error") or f"storm_runner exited with code {rc}"

        except asyncio.TimeoutError:
            d["status"] = JobStatus.FAILED
            d["finished_at"] = time.time()
            d["error"] = f"Job timed out after {settings.JOB_TIMEOUT_S}s"
            if "pid" in d and d["pid"]:
                try:
                    os.kill(d["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass

        except Exception as e:
            log.exception("Job %s crashed", job_id)
            d["status"] = JobStatus.FAILED
            d["finished_at"] = time.time()
            d["error"] = str(e)

        # Финализируем список файлов
        d["files"] = _scan_result_files(job_id)

        # Webhook
        if d.get("callback_url"):
            _fire_callback(job_id)


def _fire_callback(job_id: str):
    """POST webhook на callback_url."""
    import httpx
    d = _jobs[job_id]
    try:
        httpx.post(
            d["callback_url"],
            json={
                "id": job_id,
                "topic": d["topic"],
                "status": d["status"].value,
                "finished_at": d.get("finished_at"),
                "files": d.get("files", {}),
                "error": d.get("error"),
            },
            timeout=15,
        )
    except Exception as e:
        log.warning("Callback failed for %s → %s: %s", job_id, d["callback_url"], e)


# ────────────────────── query helpers ────────────────────────
def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _jobs.get(job_id)


def list_jobs(limit: int = 50) -> list:
    items = sorted(_jobs.values(), key=lambda x: x["created_at"], reverse=True)
    return items[:limit]


def cancel_job(job_id: str) -> bool:
    d = _jobs.get(job_id)
    if not d:
        return False
    if d["status"] in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
        return False
    pid = d.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    d["status"] = JobStatus.CANCELLED
    d["finished_at"] = time.time()
    return True


def delete_job(job_id: str) -> bool:
    d = _jobs.pop(job_id, None)
    if not d:
        return False
    import shutil
    shutil.rmtree(settings.WORKDIR_BASE / job_id, ignore_errors=True)
    shutil.rmtree(settings.OUTPUT_BASE / job_id, ignore_errors=True)
    return True


def get_log(job_id: str) -> Optional[str]:
    log_path = settings.WORKDIR_BASE / job_id / "storm.log"
    if not log_path.exists():
        return None
    return log_path.read_text(encoding="utf-8", errors="replace")


def get_file(job_id: str, filename: str) -> Optional[bytes]:
    out_dir = settings.OUTPUT_BASE / job_id
    target = (out_dir / filename).resolve()
    # Path traversal guard
    try:
        target.relative_to(out_dir.resolve())
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target.read_bytes()
