# 🔍 Code Review: STORM API — Containerized REST Service

**Объём**: ~1843 строк, 11 файлов (Python + Docker + HTML)
**Коммит**: `49039da feat: containerized REST API over Stanford STORM`
**Reviewer**: independent senior code review
**Date**: 2026-08-13

---

## Резюме

REST-обёртка над Stanford STORM с асинхронными задачами через subprocess, Docker Compose, WebUI и webhook-колбэками. Архитектура в целом здравая (subprocess isolation для dspy — правильное решение), но есть **критические проблемы безопасности**, несколько design smell-ов и ряд неиспользуемых/мёртвых полей.

**Оценка**: 🟡 Требует доработки перед продакшеном.

---

## 🔴 BLOCKER — must fix

### 1. Path Traversal в `/jobs/{job_id}/files/{file_path:path}` — ЗАЩИТА ЕСТЬ, НО НЕПОЛНАЯ

**Файл**: `app/jobs.py:263-273`, `app/main.py:213-233`

```python
# jobs.py — get_file()
def get_file(job_id: str, filename: str) -> Optional[bytes]:
    out_dir = settings.OUTPUT_BASE / job_id
    target = (out_dir / filename).resolve()
    try:
        target.relative_to(out_dir.resolve())  # ✅ guard есть
    except ValueError:
        return None
```

Хорошо, что `resolve()` + `relative_to()` на месте. **Но**: guard в `get_file()`, а endpoint в `main.py` сначала делает `_job_or_404(job_id)` — и `job_id` тоже user-controlled. Паттерн `OUTPUT_BASE / job_id` без `resolve()` на `job_id` потенциально уязвим, если `job_id` содержит `../`.

**Рекомендация**: добавить валидацию `job_id` (например, `re.match(r'^[a-f0-9]{12}$', job_id)`) на уровне endpoint. UUID hex[:12] генерируется сервером, но get_file может быть вызван с любым `job_id` от клиента.

---

### 2. SSRF через `callback_url` (webhook)

**Файл**: `app/jobs.py:220-230`

```python
def _fire_callback(job_id: str):
    httpx.post(d["callback_url"], json={...}, timeout=10)
```

`callback_url` полностью user-controlled. Атакующий может:
- Сканировать внутреннюю сеть (`http://169.254.169.254/latest/meta-data/` для AWS metadata)
- Достучаться до внутренних сервисов (`http://redis:6379/`, `http://searxng:8080/`)
- DoS внутренних сервисов через blind SSRF

**Рекомендация**:
```python
import ipaddress, urllib.parse

def _is_safe_callback_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        pass  # hostname — доменное имя, OK
    return True
```

---

### 3. CORS `allow_origins=["*"]` — открыт для всех

**Файл**: `app/main.py:44-49`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Для внутреннего API, запущенного в Docker, это приемлемо **только если** нет авторизации. Но в сочетании с отсутствием auth (пункт 4) — любой сайт в браузере пользователя может запускать STORM-задачи за счёт владельца контейнера.

**Рекомендация**: если API публично доступно — ограничить origins или добавить API key auth.

---

### 4. Нет авторизации вообще

Любой, кто может достучаться до порта 8000, может:
- Запускать задачи (тратить LLM-токены)
- Читать результаты чужих задач
- Удалять задачи
- Читать логи

**Рекомендация**: хотя бы `X-API-Key` header с проверкой через env var. Минимальный effort, максимальный эффект.

---

### 5. `.env` содержит реальный API key, но `.gitignore` не исключает его из истории

**Файл**: `.env`

```
BRAVE_API_KEY=BSAU1NFudJTN8m4SG9nLxxF0DCV68H1
```

`.env` **не в git** (проверено: `git ls-files .env` пуст), `.gitignore` исключает. ✅ Но если кто-то случайно сделает `git add .env` — ключ утечёт.

**Рекомендация**: добавить `git-secrets` или pre-commit hook, блокирующий коммиты с API key паттернами.

---

## 🟠 HIGH — should fix

### 6. `os.environ.copy()` — утечка всех переменных в subprocess

**Файл**: `app/jobs.py:128`

```python
proc = await asyncio.create_subprocess_exec(
    *cmd, ...,
    env=os.environ.copy(),  # ← ВСЁ, включая API keys
)
```

Subprocess `storm_runner.py` получает **все** env vars, включая `OPENAI_API_KEY`, `BRAVE_API_KEY` и т.д. Если runner упадёт с traceback, ключи могут попасть в лог.

**Рекомендация**: передавать только необходимые переменные:
```python
safe_env = {
    k: v for k, v in os.environ.items()
    if k.startswith(("STORM_", "OPENAI_", "ANTHROPIC_", "OLLAMA_",
                     "BRAVE_", "BING_", "YDC_", "SERPER_", "TAVILY_"))
}
proc = await asyncio.create_subprocess_exec(*cmd, env=safe_env, ...)
```

---

### 7. In-memory job store — потеря состояния при рестарте

**Файл**: `app/jobs.py:25`

```python
_jobs: Dict[str, Dict[str, Any]] = {}
```

При `docker compose restart` все задачи теряются. Запущенные subprocess-ы становятся зомби (нет reattach).

**Рекомендация**: как минимум — SQLite для persistence. Как максимум — Redis. Для v1 можно документировать ограничение.

---

### 8. `_read_log_tail` читает весь файл в память

**Файл**: `app/jobs.py:59-65`

```python
def _read_log_tail(job_id: str, lines: int = 30) -> Optional[str]:
    data = log_path.read_text(encoding="utf-8", errors="replace")
    all_lines = data.splitlines()
    return "\n".join(all_lines[-lines:])
```

STORM-лог для больших статей может быть 10+ MB. `read_text()` загружает всё в RAM.

**Рекомендация**:
```python
import collections

def _read_log_tail(job_id: str, lines: int = 30) -> Optional[str]:
    log_path = settings.WORKDIR_BASE / job_id / "storm.log"
    if not log_path.exists():
        return None
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        tail = collections.deque(f, maxlen=lines)
    return "".join(tail)
```

---

### 9. Задача-задание (`asyncio.create_task`) не сохраняется

**Файл**: `app/jobs.py:101`

```python
asyncio.create_task(_execute(job_id, req))
return job_id
```

Task не хранится нигде. При shutdown uvicorn отменяет все pending tasks молча — subprocess-ы могут остаться зомби.

**Рекомендация**:
```python
# В модуле jobs
_tasks: set[asyncio.Task] = set()

def create_job_task(job_id, req):
    task = asyncio.create_task(_execute(job_id, req))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task
```

---

### 10. Dockerfile: контейнер работает от root

**Файл**: `Dockerfile` — нет `USER` directive.

STORM runner создаёт файлы в `/data/output/` и `/data/workdir/` от root. Bind mounts на хосте тоже будут root-owned.

**Рекомендация**:
```dockerfile
RUN useradd -r -s /usr/sbin/nologin storm
RUN chown -R storm:storm /data
USER storm
```

---

### 11. Multi-stage build не уменьшает образ

**Файл**: `Dockerfile:29`

```dockerfile
FROM storm-base AS api
```

Stage 2 наследует **всё** от stage 1 (build-essential, git, chromium). Это не multi-stage в классическом смысле — это просто два этапа сборки одного образа.

Для реального уменьшения:
```dockerfile
FROM python:3.11-slim AS builder
# ... install everything ...

FROM python:3.11-slim AS runtime
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin
# ...
```

Или хотя бы удалить `build-essential` и `git` в финальном stage.

---

## 🟡 MEDIUM — nice to fix

### 12. `custom_instructions` передаётся в runner config, но runner его не использует

**Файл**: `app/jobs.py:57` — `"custom_instructions": req.custom_instructions`
**Файл**: `scripts/storm_runner.py` — нигде не читает `custom_instructions`

Мёртвое поле. StormRequest принимает, runner игнорирует.

**Рекомендация**: либо реализовать передачу в STORM (через `STORMWikiRunnerArguments`), либо удалить из модели.

---

### 13. `max_workers` — противоречивые дефолты

| Источник | Значение |
|---|---|
| `config.py` | `4` |
| `docker-compose.yml` | `2` |
| `.env` | `1` |

Три разных дефолта. Победит `.env` (последний в цепочке), но это сбивает с толку.

**Рекомендация**: привести к одному значению. Дефолт `config.py` = `2`, `.env` комментарий с пояснением.

---

### 14. Semaphore lazy init — нет защиты от гонок

**Файл**: `app/jobs.py:29-33`

```python
def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.MAX_WORKERS)
    return _semaphore
```

В asyncio single-threaded контексте это безопасно (нет concurrent access). Но если uvicorn запущен с `--workers > 1`, каждый процесс получит свой semaphore — что корректно (per-process limit). Если же используется threading — возможна гонка.

**Рекомендация**: инициализировать в `@app.on_event("startup")`, а не lazily.

---

### 15. SearXNG service: `SEARXNG_BASE_URL=http://localhost:8080` не работает из другого контейнера

**Файл**: `docker-compose.yml:48`

```yaml
environment:
  - SEARXNG_BASE_URL=http://localhost:8080
```

`storm-api` и `searxng` — разные контейнеры. `localhost:8080` внутри `storm-api` не到达 `searxng`.

**Рекомендация**: `http://searxng:8080` (Docker DNS).

---

### 16. `on_event("startup")` — deprecated в FastAPI

**Файл**: `app/main.py:260`

```python
@app.on_event("startup")
async def _startup():
```

`on_event` deprecated с FastAPI 0.109. Использовать lifespan context manager:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("STORM API starting")
    yield
    log.info("STORM API shutting down")

app = FastAPI(..., lifespan=lifespan)
```

---

### 17. Нет graceful shutdown для запущенных задач

При SIGTERM (docker stop) uvicorn завершается, но running STORM subprocess-ы просто убиваются. Нет обработки сигнала для корректного завершения.

**Рекомендация**: в lifespan shutdown phase — отправить SIGTERM всем дочерним процессам и дождаться их с timeout.

---

### 18. `_execute` ловит слишком общее `Exception`

**Файл**: `app/jobs.py:208`

```python
except Exception as e:
    log.exception("Job %s crashed", job_id)
    d["status"] = JobStatus.FAILED
```

Ловит `MemoryError`, `OSError` и т.д. Стоит ловить конкретные исключения или re-raise критические.

---

### 19. DuckDuckGo rate limiting — нет обработки

Известная проблема: после ~50 запросов DuckDuckGo начинает rate-limitить, что крашит dspy. `sitecustomize.py` патчит `giveup_hdlr`, но не предотвращает сам rate limit.

**Рекомендация**: добавить retry с exponential backoff, или документировать ограничение.

---

## 🟢 LOW — polish

### 20. `_scan_result_files` рекурсивно сканирует всю директорию

**Файл**: `app/jobs.py:79-82`

```python
for p in sorted(out_dir.rglob("*")):
```

Для больших результатов (много файлов) это может быть медленно. Не критично для v1.

---

### 21. Webhook fire на failure тоже

**Файл**: `app/jobs.py:218-219`

```python
if d.get("callback_url"):
    _fire_callback(job_id)
```

Вызывается в `finally` блоке — колбэк уйдёт и при `FAILED`. Это может быть intentional, но стоит задокументировать.

---

### 22. `httpx.post` в `_fire_callback` — синхронный вызов в async контексте

**Файл**: `app/jobs.py:224`

```python
httpx.post(d["callback_url"], json={...}, timeout=10)
```

Синхронный `httpx.post` блокирует event loop на 10 секунд (timeout). Должен быть `async`:

```python
async with httpx.AsyncClient() as client:
    await client.post(d["callback_url"], json={...}, timeout=10)
```

---

### 23. WebUI (`index.html`) — 679 строк inline JS/CSS

Монолитный HTML без CSP headers. Для v1 приемлемо, но для продакшену стоит:
- Добавить `Content-Security-Policy` header
- Вынести JS в отдельный файл (для кеширования)

---

### 24. `_build_runner_config` — несовместимый паттерн для falsy values

**Файл**: `app/jobs.py:51`

```python
"max_tokens": req.max_tokens or settings.MAX_TOKENS,
```

`or` для int: если `max_tokens=100` (min по Pydantic) — OK (truthy). Но `temperature=0.0` обработан правильно через `is not None`. Несогласованность паттернов.

**Рекомендация**: везде использовать `if x is not None else default`.

---

### 25. `sitecustomize.py` — хрупкий monkey-patching

Три monkey-patch на httpx, dsp и knowledge_storm. Работает, но:
- Ломается при обновлении httpx/dspy
- Нет тестов на совместимость
- `PYTHONPATH="/app/app"` — нетипичный паттерн

**Рекомендация**: добавить версионные проверки:
```python
assert httpx.__version__.startswith("0.28"), f"httpx {httpx.__version__} не тестировался"
```

---

## 📊 Сводка

| Severity | Count | Ключевые темы |
|---|---|---|
| 🔴 BLOCKER | 5 | SSRF, auth, CORS, API key в .env, path validation |
| 🟠 HIGH | 6 | env leak, in-memory state, root user, log memory, task leak, multi-stage |
| 🟡 MEDIUM | 8 | dead fields, defaults, deprecated API, graceful shutdown |
| 🟢 LOW | 6 | webhook sync, CSP, monkey-patch fragility |

## ✅ Что сделано хорошо

1. **Subprocess isolation** — правильное решение для dspy (глобальное состояние)
2. **Path traversal guard** в `get_file()` — `resolve()` + `relative_to()`
3. **sitecustomize.py** — pragmatic workaround для upstream багов (Wikipedia 403, DuckDuckGo crash)
4. **Semaphore** для ограничения параллелизма
5. **Per-job work/output dirs** — чистая изоляция
6. **WebUI** — удобно для ручного тестирования
7. **Pydantic модели** с валидацией (min_length, ge/le constraints)
8. **Async subprocess** с streaming stdout → log file

## 🎯 Приоритет исправлений

1. **SSRF** (#2) — критично, быстро фиксится
2. **Auth** (#4) — хотя бы API key
3. **Dockerfile USER** (#10) — one-liner
4. **SearXNG URL** (#15) — one-liner
5. **Env leak** (#6) — small refactor
6. **Async webhook** (#22) — small refactor
7. Остальное — по мере приближения к продакшену
