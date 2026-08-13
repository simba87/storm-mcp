#!/usr/bin/env python3
"""
STORM runner — вызывается как subprocess из API-сервиса.

Читает JSON-конфигурацию из stdin, запускает STORMWikiRunner,
пишет результат в output_dir.

Usage:
    echo '{"topic": "...", ...}' | python storm_runner.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

# Явно активируем httpx monkey-patch (User-Agent + timeout) ДО любого импорта knowledge_storm.
# sitecustomize.py срабатывает автоматически при наличии на PYTHONPATH,
# но явный импорт — гарантия для subprocess.
try:
    import sitecustomize  # noqa: F401
except Exception:
    pass


def _setup_dspy_logging():
    """Подавляем шумные dspy логи, оставляем warnings+."""
    import logging
    for name in ("dspy", "httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _build_lm_configs(provider: str, model_name: str, temp: float,
                      max_tokens: int):
    """Создаёт конфигурацию языковых моделей для STORM."""
    from knowledge_storm import STORMWikiLMConfigs
    from knowledge_storm.lm import (
        OpenAIModel, ClaudeModel, LitellmModel, OllamaClient,
    )

    lm_configs = STORMWikiLMConfigs()

    def _apply(model):
        """Привязывает одну модель ко всем 5 ролям STORM."""
        lm_configs.set_conv_simulator_lm(model)
        lm_configs.set_question_asker_lm(model)
        lm_configs.set_outline_gen_lm(model)
        lm_configs.set_article_gen_lm(model)
        lm_configs.set_article_polish_lm(model)

    if provider == "claude":
        claude_kwargs = {
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        claude_kwargs = {k: v for k, v in claude_kwargs.items() if v is not None}
        try:
            _apply(ClaudeModel(model=model_name, **claude_kwargs))
        except Exception as e:
            print(f"[runner] Warning: Claude model setup failed: {e}", file=sys.stderr, flush=True)

    elif provider == "ollama":
        # Нативная Ollama-интеграция STORM — использует OllamaClient
        # port и url парсятся из OPENAI_API_BASE или дефолтят на localhost:11434
        import re
        api_base = os.getenv("OPENAI_API_BASE", "http://localhost:11434/v1")
        m = re.match(r"https?://([^:]+):(\d+)", api_base)
        if m:
            ollama_url = f"http://{m.group(1)}"
            ollama_port = int(m.group(2))
        else:
            ollama_url, ollama_port = "http://localhost", 11434

        try:
            model = OllamaClient(
                model=model_name,
                url=ollama_url,
                port=ollama_port,
                model_type="chat",
                stop_tokens=["</s>", "<|im_end|>", "<|end|>"],
                temperature=temp,
                max_tokens=max_tokens,
                timeout_s=int(os.getenv("OLLAMA_TIMEOUT", "600")),     # 10 мин на inference
                num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),      # окно контекста
            )
            _apply(model)
            print(f"[runner] Ollama model: {model_name} at {ollama_url}:{ollama_port} "
                  f"(timeout={model.timeout_s}s, ctx={model.kwargs.get('num_ctx', '?')})",
                  flush=True)
        except Exception as e:
            print(f"[runner] Warning: Ollama setup failed: {e}", file=sys.stderr, flush=True)

    elif provider == "litellm":
        # Litellm — поддержка 100+ провайдеров
        # model_name должно быть в litellm-формате: openai/gpt-4o, anthropic/claude-3-5-sonnet, etc.
        litellm_kwargs = {
            "model": model_name,
            "api_key": os.getenv("OPENAI_API_KEY"),
            "model_type": "chat",
        }
        litellm_kwargs = {k: v for k, v in litellm_kwargs.items() if v is not None}
        try:
            _apply(LitellmModel(**litellm_kwargs))
        except Exception as e:
            print(f"[runner] Warning: Litellm setup failed: {e}", file=sys.stderr, flush=True)

    else:
        # OpenAI / любой OpenAI-compatible endpoint
        api_key = os.getenv("OPENAI_API_KEY")
        api_base = os.getenv("OPENAI_API_BASE")

        openai_kwargs = {
            "api_key": api_key,
            "model_type": "chat",
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        if api_base:
            openai_kwargs["api_base"] = api_base
        openai_kwargs = {k: v for k, v in openai_kwargs.items() if v is not None}
        try:
            _apply(OpenAIModel(model=model_name, **openai_kwargs))
        except Exception as e:
            print(f"[runner] Warning: OpenAI model setup failed: {e}", file=sys.stderr, flush=True)

    return lm_configs


def _build_retriever(engine: str, top_k: int):
    """Создаёт retrieval-менеджер (поисковый движок)."""
    from knowledge_storm.rm import (
        YouRM, BingSearch, BraveRM, SerperRM,
        DuckDuckGoSearchRM, TavilySearchRM, SearXNG,
    )

    engine = engine.lower()
    common = {"k": top_k}

    if engine == "you":
        return YouRM(ydc_api_key=os.getenv("YDC_API_KEY"), **common)
    elif engine == "bing":
        return BingSearch(bing_search_api_key=os.getenv("BING_SEARCH_API_KEY"), **common)
    elif engine == "brave":
        return BraveRM(brave_search_api_key=os.getenv("BRAVE_API_KEY"), **common)
    elif engine == "serper":
        return SerperRM(serper_api_key=os.getenv("SERPER_API_KEY"), **common)
    elif engine == "duckduckgo":
        return DuckDuckGoSearchRM(**common)
    elif engine == "tavily":
        return TavilySearchRM(tavily_api_key=os.getenv("TAVILY_API_KEY"), **common)
    elif engine == "searxng":
        searxng_url = os.getenv("STORM_SEARXNG_URL", "http://localhost:8080")
        return SearXNG(searxng_url=searxng_url, **common)
    else:
        return DuckDuckGoSearchRM(**common)


def run(cfg: dict) -> dict:
    _setup_dspy_logging()

    from knowledge_storm import (
        STORMWikiRunnerArguments,
        STORMWikiRunner,
    )

    output_dir = Path(cfg["output_dir"])
    work_dir   = Path(cfg["work_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # ── Аргументы раннера (topic НЕ здесь — в runner.run()) ──
    runner_args = STORMWikiRunnerArguments(
        output_dir=str(output_dir),
        max_conv_turn=cfg.get("max_conv_steps", 3),
        search_top_k=cfg.get("search_top_k", 3),
    )

    # ── LM конфигурация ──
    lm_configs = _build_lm_configs(
        provider=cfg.get("llm_provider", "openai"),
        model_name=cfg.get("model_name", "gpt-4o"),
        temp=cfg.get("temperature", 1.0),
        max_tokens=cfg.get("max_tokens", 500),
    )

    # ── Retriever ──
    retriever = _build_retriever(
        engine=cfg.get("search_engine", "duckduckgo"),
        top_k=cfg.get("search_top_k", 3),
    )

    # ── Запуск ──
    # Сигнатура: STORMWikiRunner(args, lm_configs, rm)
    runner = STORMWikiRunner(runner_args, lm_configs, rm=retriever)

    # do_polish_article передаётся в run(), не в RunnerArguments
    do_polish = cfg.get("do_polish", True)

    runner.run(
        topic=cfg["topic"],
        do_polish_article=do_polish,
    )
    runner.post_run()

    # ── Результат: директория topic_name внутри output_dir ──
    topic_dirs = [d for d in output_dir.iterdir() if d.is_dir()]
    result_dir = str(topic_dirs[0]) if topic_dirs else str(output_dir)

    files = {}
    result_path = Path(result_dir)
    if result_path.exists():
        for p in sorted(result_path.rglob("*")):
            if p.is_file():
                files[p.name] = str(p)

    return {
        "status": "done",
        "result_dir": result_dir,
        "files": files,
    }


def main():
    try:
        cfg = json.load(sys.stdin)
        print(f"[runner] Starting STORM for topic: {cfg['topic']}", flush=True)
        result = run(cfg)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        sys.exit(0)
    except Exception as e:
        traceback.print_exc()
        print(json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
