"""Ruft die verfügbaren Ollama-Cloud-Modelle von ollama.com ab und cached sie.

Der lokale Daemon kennt nur heruntergeladene Modelle; die volle Cloud-Liste
lebt auf ollama.com. Wir scrapen die Cloud-Such-Seite und pro Modell die echten
``*cloud*``-Tags (Schema variiert: meist ``:cloud``, bei gpt-oss/qwen3-coder
z. B. ``:120b-cloud``). Ergebnis wird gecacht, damit der Start schnell bleibt.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

SEARCH_URL = "https://ollama.com/search?c=cloud"
TAGS_URL = "https://ollama.com/library/{}/tags"
CACHE_PATH = Path.home() / ".klotho" / "cloud_models.json"
CACHE_TTL = 7 * 86400  # 7 Tage
_UA = "KlothoTUI (+https://github.com/Nick-Wolf-HLK/Klotho-)"


def _base_names(timeout: float) -> list[str]:
    r = httpx.get(SEARCH_URL, headers={"User-Agent": _UA}, timeout=timeout)
    r.raise_for_status()
    return sorted(set(re.findall(r'href="/library/([^"/]+)"', r.text)))


def _cloud_tags(name: str, timeout: float) -> list[str]:
    try:
        r = httpx.get(TAGS_URL.format(name), headers={"User-Agent": _UA}, timeout=timeout)
        r.raise_for_status()
        tags = re.findall(rf"{re.escape(name)}:[A-Za-z0-9._-]+", r.text)
        return [t for t in set(tags) if "cloud" in t]
    except Exception:
        return []


def fetch_cloud_models(timeout: float = 15.0) -> list[str]:
    """Live von ollama.com: alle Cloud-Modell-Tags (kann ~Sekunden dauern)."""
    names = _base_names(timeout)
    out: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for tags in ex.map(lambda n: _cloud_tags(n, timeout), names):
            out.extend(tags)
    return sorted(set(out))


def refresh_cache(timeout: float = 15.0) -> list[str]:
    """Holt die Cloud-Modelle und schreibt den Cache."""
    models = fetch_cloud_models(timeout)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({"ts": int(time.time()), "models": models}))
    return models


def cached_cloud_models(max_age: float = CACHE_TTL) -> list[str]:
    """Gibt die gecachten Cloud-Modelle zurück (leer, wenn kein/alter Cache)."""
    if not CACHE_PATH.exists():
        return []
    try:
        data = json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if max_age and (time.time() - data.get("ts", 0)) > max_age:
        return []  # zu alt → über `klotho models --refresh` erneuern
    return list(data.get("models", []))


def cache_is_stale() -> bool:
    return not cached_cloud_models()
