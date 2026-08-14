# STORM API

Контейнеризированный REST-сервис над [Stanford STORM](https://github.com/stanford-oval/storm) — запускайте длительные исследования (генерацию энциклопедических статей) через HTTP API.

## Возможности

- ✅ **Асинхронные задачи** — POST `/research` возвращает `job_id`, результат забираете позже
- ✅ **Polling статуса** — `GET /jobs/{id}` показывает прогресс
- ✅ **Лог стриминг** — `GET /jobs/{id}/log` возвращает полный stdout STORM
- ✅ **Скачивание результатов** — статьи, outline, источники доступны по URL
- ✅ **Webhook** — `callback_url` опционально уведомляет о завершении
- ✅ **Гибкая конфигурация** — LLM и поиск задаются через env, переопределяются per-request
- ✅ **Поддержка провайдеров**: OpenAI, Anthropic Claude, **Ollama** (нативно), litellm, любой OpenAI-compatible endpoint
- ✅ **Поисковые движки**: you.com, Brave, Bing, Serper, Tavily, DuckDuckGo (free), SearXNG (self-hosted)
- ✅ **Два режима**: локальная Ollama (`network_mode: host`) или удалённый API провайдер
- 🔜 **MCP server** (roadmap) — REST API готов как база для MCP-инструментов, см. [раздел MCP](#mcp-server-roadmap)

## Быстрый старт

```bash
# 1. Клонировать
git clone <your-repo-url> storm-api
cd storm-api

# 2. Выбрать сценарий (см. ниже) и настроить .env
cp .env.example .env

# 3. Собрать и запустить
docker compose up -d --build

# 4. Проверить
curl http://localhost:8000/health
# → {"status":"ok","jobs_total":0}
```

Swagger UI: http://localhost:8000/docs

**WebUI** (для ручного тестирования): http://localhost:8000/ui

## Использование

### Запустить исследование

```bash
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "History of quantum computing"}'
```

```json
{
  "id": "a1b2c3d4e5f6",
  "topic": "History of quantum computing",
  "status": "pending",
  "created_at": 1700000000.0
}
```

### Проверить статус

```bash
curl http://localhost:8000/jobs/a1b2c3d4e5f6
```

```json
{
  "id": "a1b2c3d4e5f6",
  "status": "running",
  "pid": 42,
  "started_at": 1700000001.0,
  ...
}
```

### Получить результат (когда status = "done")

```bash
curl http://localhost:8000/jobs/a1b2c3d4e5f6/result
```

```json
{
  "files": {
    "History_of_quantum_computing/storm_gen_article.txt": "/jobs/a1b2c3d4e5f6/files/History_of_quantum_computing/storm_gen_article.txt",
    "History_of_quantum_computing/storm_gen_article_polished.txt": "/jobs/.../storm_gen_article_polished.txt",
    "History_of_quantum_computing/storm_gen_outline.txt": "/jobs/.../storm_gen_outline.txt",
    "History_of_quantum_computing/url_to_info.json": "/jobs/.../url_to_info.json"
  }
}
```

### Скачать конкретный файл

```bash
curl http://localhost:8000/jobs/a1b2c3d4e5f6/files/History_of_quantum_computing/storm_gen_article_polished.txt
```

## Полный список endpoints

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/research` | Запустить новое исследование |
| `GET` | `/jobs` | Список задач (новые первыми) |
| `GET` | `/jobs/{id}` | Статус конкретной задачи |
| `GET` | `/jobs/{id}/log` | Лог выполнения (stdout STORM) |
| `GET` | `/jobs/{id}/result` | Список файлов результата |
| `GET` | `/jobs/{id}/files/{path}` | Скачать файл результата |
| `POST` | `/jobs/{id}/cancel` | Отменить задачу (SIGTERM) |
| `DELETE` | `/jobs/{id}` | Удалить задачу и все файлы |

## Расширенная конфигурация запроса

```json
{
  "topic": "Impact of transformer architecture on NLP",
  "custom_instructions": "Focus on practical applications, not theory. Include citations.",
  "model_name": "anthropic/claude-3-5-sonnet-20241022",
  "temperature": 0.7,
  "max_tokens": 1000,
  "search_engine": "brave",
  "search_top_k": 5,
  "do_polish": true,
  "max_conv_steps": 4,
  "callback_url": "https://your-app.com/webhooks/storm-complete"
}
```

## Сценарий 1. Локальная модель через Ollama

Полностью локальный запуск — без внешних API ключей. STORM использует нативный `OllamaClient` (прямой `/api/generate`, не OpenAI-прокси).

### Шаг 1. Установить Ollama и модель

```bash
# Любая модель с chat completions
ollama pull ornith:35b          # или llama3.1:8b, qwen2.5:7b — меньше = быстрее
ollama serve                    # по умолчанию localhost:11434
```

### Шаг 2. Настроить `.env`

```bash
# ── LLM ──
STORM_LLM_PROVIDER=ollama
OPENAI_API_BASE=http://localhost:11434/v1
STORM_MODEL_NAME=ornith:35b
STORM_TEMPERATURE=0.7
STORM_MAX_TOKENS=2000

# ── Поиск ──
STORM_SEARCH_ENGINE=duckduckgo    # бесплатно, без API key

# ── Ollama tuning ──
OLLAMA_TIMEOUT=600                # timeout одного inference (сек) — 35B на CPU = долго
OLLAMA_NUM_CTX=8192               # окно контекста

# ── Runner ──
STORM_JOB_TIMEOUT=7200            # общий timeout задачи (2 часа)
STORM_MAX_WORKERS=1               # одна задача одновременно
```

### Шаг 3. Запустить

`docker-compose.yml` уже настроен с `network_mode: host` — контейнер видит Ollama на `localhost:11434`.

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

### Шаг 4. Запустить исследование

```bash
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "What is quantum entanglement", "max_conv_steps": 1}'
```

> ⚠️ **Большие модели на CPU медленные.** ornith:35b (~15 t/s на 8 ядрах) генерирует одну статью 30–60 мин. Полный pipeline STORM делает ~50–100 LLM вызовов. Для быстрых тестов — `llama3.1:8b` или `qwen2.5:7b` (5–10 мин).
>
> ⚠️ **DuckDuckGo rate-limit.** Бесплатный DuckDuckGo лимитирует после ~50 поисков за короткий промежуток. Один полный прогон STORM делает 30–100 поисков — вы упрётесь в лимит, задача упадёт с ошибкой `'DuckDuckGoSearchException' object has no attribute 'message'` (баг в dsp exception handler, обходится нашим `sitecustomize.py`, но поиск всё равно перестаёт отдавать результаты → `cosine_similarity` падает на пустых snippets). Проверено на практике. Для стабильной работы используйте платный API (you.com, **Brave** — протестирован, Tavily) или self-hosted SearXNG.

---

## Сценарий 2. Удалённый провайдер (OpenAI-compatible API)

Любой провайдер с OpenAI-compatible endpoint: OpenAI, Azure OpenAI, Together AI, Groq, DeepSeek, Anyscale, vLLM, LiteLLM и т.д.

### Что нужно

Три значения от провайдера:

| Параметр | Пример |
|---|---|
| **Base URL** | `https://api.openai.com/v1`, `https://api.deepseek.com/v1`, `https://api.groq.com/openai/v1` |
| **API Key** | `sk-...`, `gsk_...` |
| **Model name** | `gpt-4o`, `deepseek-chat`, `llama-3.3-70b-versatile` |

### Шаг 1. Настроить `.env`

```bash
# ── LLM ──
STORM_LLM_PROVIDER=openai                   # для любого OpenAI-compatible endpoint
OPENAI_API_KEY=gsk_...                      # ваш ключ
OPENAI_API_BASE=https://api.groq.com/openai/v1   # base URL провайдера
STORM_MODEL_NAME=llama-3.3-70b-versatile    # модель провайдера
STORM_TEMPERATURE=1.0
STORM_MAX_TOKENS=500

# ── Поиск ──
STORM_SEARCH_ENGINE=you                     # или brave, tavily, bing, serper
YDC_API_KEY=your-you-com-api-key            # ключ поискового провайдера

# ── Runner ──
STORM_JOB_TIMEOUT=1800                      # удалённые API быстрые — 30 мин хватит
STORM_MAX_WORKERS=2
```

### Популярные провайдеры

| Провайдер | `OPENAI_API_BASE` | Пример модели | Notes |
|---|---|---|---|
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o`, `gpt-4o-mini` | Дефолт, `OPENAI_API_BASE` можно не указывать |
| **Groq** | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` | Очень быстро, есть free tier |
| **DeepSeek** | `https://api.deepseek.com/v1` | `deepseek-chat` | Дёшево, хорошо рассуждает |
| **Together AI** | `https://api.together.xyz/v1` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | Open-source модели |
| **OpenRouter** | `https://openrouter.ai/api/v1` | `anthropic/claude-3.5-sonnet` | Агрегатор, 100+ моделей |
| **vLLM** (self-hosted) | `http://your-server:8000/v1` | любая | Локальный GPU inference |

### Шаг 2. Настроить docker-compose.yml

**Сценарий 1 (Ollama локально)** требует `network_mode: host`:

```yaml
services:
  storm-api:
    network_mode: host   # ← раскомментировать для Ollama
```

**Сценарий 2 (удалённый провайдер)** — `network_mode: host` **не нужен** и должен быть закомментирован (по умолчанию так и есть):

```yaml
services:
  storm-api:
    # network_mode: host   # ← закомментировано (стандартная bridge-сеть)
```

> ✅ **По умолчанию** в `docker-compose.yml` используется bridge-сеть (подходит для удалённых провайдеров).
> Для Ollama нужно явно включить `network_mode: host`.

Bind mounts (`./data/output:/data/output`) работают в обоих сценариях — файлы всегда на хосте.

### Шаг 3. Запустить исследование

```bash
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "History of quantum computing"}'
```

Удалённые API (Groq, OpenAI) в 10–100× быстрее CPU Ollama — статья генерируется за 2–10 мин.

---

## Переключение search-провайдера

Поиск не зависит от LLM и настраивается отдельно через `STORM_SEARCH_ENGINE`:

| Engine | env key | Бесплатно |
|---|---|---|
| `duckduckgo` | — | ✅ (но rate-limit после ~50 запросов — см. предупреждение выше; полный STORM-прогон упадёт) |
| `you` | `YDC_API_KEY` | нет |
| `brave` | `BRAVE_API_KEY` | нет |
| `tavily` | `TAVILY_API_KEY` | нет |
| `bing` | `BING_SEARCH_API_KEY` | нет |
| `serper` | `SERPER_API_KEY` | нет |
| `searxng` | `STORM_SEARXNG_URL` | ✅ self-hosted |

Для SearXNG (полностью приватный, без лимитов):

```bash
docker compose --profile offline up -d   # поднимет SearXNG контейнер
# .env: STORM_SEARCH_ENGINE=searxng
# .env: STORM_SEARXNG_URL=http://localhost:8080
```

## WebUI

Встроенный визуальный интерфейс для ручного управления задачами — удобно при тестировании.

**URL:** http://localhost:8000/ui

Возможности:
- 📝 **Создание задачи** — форма с topic, инструкциями, и сворачиваемыми advanced-настройками (provider, model, temperature, search engine и т.д.)
- 📋 **Список задач** — sidebar со всеми задачами, авто-refresh каждые 5 сек, статусы цветными бейджами
- ℹ️ **Детали задачи** — ID, PID, timestamps, duration, error, return code
- 📄 **Лог** — авто-polling (3 сек), авто-scroll вниз
- 📊 **Результат** — список файлов, просмотр содержимого (статьи в читаемом виде, JSON/логи в mono)
- ⏹ **Cancel / 🗑 Delete** — кнопки в один клик
- ● **Status badge** в шапке — live-индикатор API + текущая конфигурация

Никаких внешних зависимостей — чистый HTML/CSS/JS, раздаётся FastAPI как static.

## MCP server (roadmap)

REST API спроектирован как база для **MCP (Model Context Protocol) сервера** — цель проекта (`IDEA.md`). MCP-слой будет тонким адаптером поверх готовых endpoints:

```
LLM-агент (Hermes / Claude Desktop / Cursor)
    │  MCP protocol (stdio)
    ▼
mcp_server.py  ← ~150 строк: валидация аргументов → REST-вызовы
    │  HTTP (X-API-Key)
    ▼
storm-api (этот контейнер)
```

### Планируемые MCP tools

| Tool | REST endpoint | Назначение |
|---|---|---|
| `storm_research` | `POST /research` | Запустить исследование (неблокирующий, вернуть `job_id`) |
| `storm_job_status` | `GET /jobs/{id}` | Статус, duration, error |
| `storm_job_log` | `GET /jobs/{id}/log` | Хвост лога (tail N строк) |
| `storm_get_article` | `GET /jobs/{id}/files/.../storm_gen_article_polished.txt` | Финальный текст статьи |
| `storm_list_sources` | `GET /jobs/{id}/files/.../url_to_info.json` | Источники исследования |
| `storm_list_jobs` | `GET /jobs` | Список задач |
| `storm_cancel_job` | `POST /jobs/{id}/cancel` | Отмена |

### Почему поверх REST, а не напрямую к STORM

- **Job-модель** (`pending → running → done/failed`) уже ложится на MCP-паттерн «запусти → полли → забери результат» — генерация статьи занимает 5–30+ мин, MCP tool не должен блокироваться
- **Subprocess-изоляция** dspy/STORM уже решена внутри контейнера
- **Auth** (`X-API-Key`) — MCP-сервер просто пробрасывает ключ из env
- REST API остаётся единственным источником правды: его можно использовать и без MCP (curl, WebUI, интеграции)

### Вариант транспортировки

Планируется **stdio** (`mcpServers` entry в конфиге агента) — максимальная совместимость. Альтернатива — MCP endpoint внутри FastAPI (`/mcp`, streamable HTTP), один контейнер на всё, но требует MCP-клиента с HTTP-транспортом.

## Архитектура

```
┌─────────────────────────────────────────────────┐
│  FastAPI (uvicorn :8000)                        │
│  ┌───────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ REST API  │→ │ JobStore │→ │ AsyncIO Pool │ │
│  │ /research │  │ (memory) │  │ (Semaphore)  │ │
│  └───────────┘  └──────────┘  └──────┬───────┘ │
│                                      │          │
│                          ┌───────────▼────────┐ │
│                          │ subprocess          │ │
│                          │ storm_runner.py     │ │
│                          │  ┌──────────────┐  │ │
│                          │  │ knowledge_   │  │ │
│                          │  │ storm (STORM)│  │ │
│                          │  └──────┬───────┘  │ │
│                          └─────────┼──────────┘ │
└─────────────────────────────────────┼───────────┘
                                      │
                     ┌────────────────▼───────────┐
                     │  /data/output/{job_id}/    │
                     │    {topic}/                │
                     │      storm_gen_article.txt │
                     │      storm_gen_outline.txt │
                     │      url_to_info.json      │
                     └────────────────────────────┘
```

**Почему subprocess, а не in-process?**
- `knowledge_storm` использует dspy + litellm, которые создают глобальное состояние
- Изоляция процессов = убираем утечки между задачами
- OOM/crash одной задачи не роняет API

## Переменные окружения

См. [`.env.example`](.env.example) — полный список с комментариями.

| Переменная | Default | Описание |
|---|---|---|
| `STORM_LLM_PROVIDER` | `openai` | `openai` / `claude` / `litellm` |
| `STORM_MODEL_NAME` | `openai/gpt-4o` | litellm-формат |
| `STORM_SEARCH_ENGINE` | `duckduckgo` | Free option, см. выше |
| `STORM_MAX_WORKERS` | `2` | Параллельных задач |
| `STORM_JOB_TIMEOUT` | `1800` | Timeout (секунды) |
| `STORM_MAX_CONV_STEPS` | `3` | Шагов perspective-driven conversation |

## Где хранятся результаты

**Результаты задач** (статьи, outline, источники) сохраняются в:

```
/data/output/{job_id}/{topic_name}/
├── storm_gen_article.txt           # черновик статьи
├── storm_gen_article_polished.txt  # финальная версия
├── storm_gen_outline.txt           # сгенерированный outline
├── direct_gen_outline.txt          # прямой outline
├── conversation_log.json           # логи perspective conversation
├── raw_search_results.json         # сырые результаты поиска
├── url_to_info.json                # источники (URL → metadata)
└── llm_call_history.jsonl          # история LLM вызовов
```

### Доступ на хосте

В `docker-compose.yml` используются **bind mounts**:

```yaml
volumes:
  - ./data/output:/data/output
  - ./data/workdir:/data/workdir
```

**Файлы доступны на хосте** по пути `./data/output/{job_id}/` (относительно корня репозитория).

> ✅ **Работает в обоих сценариях**:
> - **Ollama локально** (`network_mode: host`) — bind mounts работают
> - **Удалённый провайдер** (bridge-сеть) — bind mounts работают
>
> Файлы переживают `docker compose down` и доступны прямо на хосте для бэкапа/копирования.

### In-memory job store

**Список задач (jobs)** хранится **в памяти API-сервиса**. При рестарте контейнера (`docker compose down && up`) job list сбрасывается.

> ⚠️ **Важно**: файлы результатов сохраняются в `./data/output/`, но WebUI (`/ui`) не покажет старые задачи после рестарта.
> Доступ к файлам возможен напрямую через HTTP API: `GET /jobs/{id}/files/{path}` (если job ещё в памяти) или через файловую систему хоста.

## Локальная разработка

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install knowledge-storm

# Запуск API (требует OPENAI_API_KEY в окружении)
uvicorn app.main:app --reload --port 8000
```

## Лицензия

MIT
