"""
Sitecustomize — автоматически выполняется при старте Python.
Три monkey-patch для исправления багов knowledge-storm:

1. httpx.Client — User-Agent + follow_redirects + timeout (fix Wikipedia 403)
2. dsp giveup_hdlr — не падать на DuckDuckGoSearchException без .message
3. get_wiki_page_title_and_toc — чистка мусорных URL + UA + None guard
"""
import httpx
import re
import requests
from bs4 import BeautifulSoup

# ════════════════ 1. httpx User-Agent patch ════════════════
_ORIG_HTTPX_INIT = httpx.Client.__init__
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _patched_httpx_init(self, *args, **kwargs):
    headers = dict(_DEFAULT_HEADERS)
    if kwargs.get("headers"):
        headers.update(kwargs["headers"])
    kwargs["headers"] = headers
    kwargs.setdefault("follow_redirects", True)
    if not kwargs.get("timeout"):
        kwargs["timeout"] = _DEFAULT_TIMEOUT
    _ORIG_HTTPX_INIT(self, *args, **kwargs)


httpx.Client.__init__ = _patched_httpx_init


# ════════════════ 2. dsp giveup_hdlr fix ════════════════
def _patch_dsp_giveup():
    try:
        import dsp.modules.mistral as _mistral
        if hasattr(_mistral, "giveup_hdlr") and not getattr(_mistral, "_patched", False):
            _orig = _mistral.giveup_hdlr

            def _safe_giveup_hdlr(details):
                try:
                    return _orig(details)
                except (AttributeError, TypeError):
                    return True

            _mistral.giveup_hdlr = _safe_giveup_hdlr
    except ImportError:
        pass


_patch_dsp_giveup()


# ════════════════ 3. get_wiki_page_title_and_toc fix ════════════════
# LLM генерирует URL с мусором (скобки, запятые): https://.../Woodcut)
# Оригинал: requests.get(url) без UA → 403; soup.find("h1") → None → .text crash
_WIKI_UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}
_WIKI_EXCLUDED = {"Contents", "See also", "Notes", "References", "External links"}


def _safe_get_wiki_page(url):
    """Чистит URL, добавляет UA, возвращает ("","") при ошибке/404."""
    # Чистим мусор в конце: ), ], }, ',', ';', '.', '>', пробелы
    url = re.sub(r"[\)\]\}\,;\.>\s]+$", "", url.strip())

    try:
        response = requests.get(url, headers=_WIKI_UA, timeout=15)
        if response.status_code != 200:
            return "", ""
        soup = BeautifulSoup(response.content, "html.parser")

        h1 = soup.find("h1")
        main_title = (
            h1.text.replace("[edit]", "").strip().replace("\xa0", " ") if h1 else ""
        )

        toc = ""
        for header in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
            level = int(header.name[1])
            section = header.text.replace("[edit]", "").strip().replace("\xa0", " ")
            if section in _WIKI_EXCLUDED:
                continue
            toc += f"{'  ' * (level - 2)}- {section}\n"

        return main_title, toc
    except Exception:
        return "", ""


def _patch_wiki_page():
    try:
        import knowledge_storm.storm_wiki.modules.persona_generator as _pg
        _pg.get_wiki_page_title_and_toc = _safe_get_wiki_page
    except ImportError:
        pass


_patch_wiki_page()
