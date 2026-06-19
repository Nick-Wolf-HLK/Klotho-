"""Token-Kompression für Klothos Pipeline-Payloads.

Inspiriert von **TSCG** (Furkan Sakizli / SKZL-AI, https://github.com/SKZL-AI/tscg)
und der **pi-tscg**-Extension, die Tool-Schemas für function-calling-Agenten
komprimieren. Klotho nutzt kein function-calling — der Token-Hotspot sind
stattdessen die Subagenten-Antworten, die sowohl an den Judge ALS AUCH an den
Synthesizer in voller Länge gehen. Diese Schicht komprimiert genau dort,
deterministisch und (im Default) verlustarm.

Stufen:
- "off"        : keine Kompression.
- "safe"       : verlustarm — trailing Whitespace entfernen, 3+ Leerzeilen → 1.
                 Code-Einrückung und Inhalt bleiben unangetastet.
- "aggressive" : zusätzlich sehr lange Antworten kappen (mit explizitem Marker,
                 analog zu pi-tscgs tool-result truncation).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

LEVELS = ("off", "safe", "aggressive")
_AGGRESSIVE_MAX_TOKENS = 400

_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)
_MANY_BLANKS = re.compile(r"\n{3,}")


def estimate_tokens(text: str) -> int:
    """Grobe, deterministische Token-Schätzung (~4 Zeichen/Token)."""
    return max(0, len(text) // 4) if text else 0


@dataclass
class CompressionStats:
    """Sammelt Vorher/Nachher-Tokenmengen über die ganze Pipeline."""

    level: str = "safe"
    before: int = 0
    after: int = 0

    def add(self, before_text: str, after_text: str) -> None:
        self.before += estimate_tokens(before_text)
        self.after += estimate_tokens(after_text)

    @property
    def saved(self) -> int:
        return self.before - self.after

    @property
    def ratio(self) -> float:
        return (self.saved / self.before) if self.before else 0.0


def _safe(text: str) -> str:
    text = _TRAILING_WS.sub("", text)
    text = _MANY_BLANKS.sub("\n\n", text)
    return text.strip()


def _aggressive(text: str) -> str:
    text = _safe(text)
    if estimate_tokens(text) <= _AGGRESSIVE_MAX_TOKENS:
        return text
    budget = _AGGRESSIVE_MAX_TOKENS * 4  # Zeichen-Budget
    cut = text[:budget]
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    return cut.rstrip() + "\n…[TSCG: gekürzt]"


def compress_text(text: str, level: str = "safe", *, stats: CompressionStats | None = None) -> str:
    """Komprimiert einen Freitext-Block gemäß Stufe; aktualisiert Stats."""
    if not text:
        out = text
    elif level == "aggressive":
        out = _aggressive(text)
    elif level == "safe":
        out = _safe(text)
    else:  # "off" oder unbekannt
        out = text
    if stats is not None:
        stats.add(text, out)
    return out


def compact_json(obj) -> str:
    """JSON kompakt serialisieren (statt indent=2) — spart Schema-Tokens."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
