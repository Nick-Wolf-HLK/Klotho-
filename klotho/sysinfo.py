"""System-Check für lokale Modelle: passt ein lokales Ollama-Modell in den RAM?

Lokale Modelle laufen auf dem eigenen Rechner — ein zu großes Modell (oder mehrere
gleichzeitig) sprengt den Arbeitsspeicher. Hier wird der RAM ermittelt und die
Modellgrößen aus Ollamas /api/tags gelesen, um nur lauffähige Modelle anzubieten.
"""
from __future__ import annotations

import os
import platform
import subprocess

import httpx

RAM_HEADROOM = 0.7        # so viel des Gesamt-RAM darf ein Modell höchstens belegen


def total_ram_bytes() -> int:
    """Gesamt-RAM des Systems in Bytes (0, wenn nicht ermittelbar)."""
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True, timeout=3)
            return int(out.stdout.strip())
    except Exception:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def _ollama_root(base_url: str) -> str:
    """Aus der OpenAI-kompatiblen base_url (…/v1) die Ollama-Wurzel ableiten."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root.rstrip("/")


def local_model_sizes(base_url: str) -> dict[str, int]:
    """Installierte lokale Modelle → Größe in Bytes (aus Ollama /api/tags)."""
    try:
        resp = httpx.get(f"{_ollama_root(base_url)}/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    out: dict[str, int] = {}
    for m in data.get("models", []):
        name = m.get("name") or m.get("model")
        if name:
            out[name] = int(m.get("size", 0) or 0)
    return out


def runnable_local_models(
    candidates: list[str],
    sizes: dict[str, int],
    ram: int,
    *,
    headroom: float = RAM_HEADROOM,
) -> tuple[list[str], list[str]]:
    """Teilt lokale Modelle in (lauffähig, zu_groß) anhand des RAM-Budgets.
    Ist RAM oder Größe unbekannt, gilt ein Modell als lauffähig (kein Ausschluss
    auf Verdacht)."""
    budget = ram * headroom if ram else 0
    runnable: list[str] = []
    too_big: list[str] = []
    for m in candidates:
        size = sizes.get(m) or sizes.get(m.split(":")[0], 0)
        if not budget or not size or size <= budget:
            runnable.append(m)
        else:
            too_big.append(m)
    return runnable, too_big


def fmt_gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"
