"""Deterministische Verifikation von Audit-Befunden gegen den echten Quellcode.

Jeder Befund behauptet „in Datei X, Zeile N steht dieser Code". Hier wird das
ohne LLM geprüft: Die Datei wird gelesen, das wörtliche Zitat normalisiert und
gegen die Quelle abgeglichen. Befunde, deren Zitat NIRGENDS in der Datei steht,
sind halluziniert und werden verworfen. Steht das Zitat an einer anderen Zeile,
wird die Zeilennummer korrigiert. So verschwinden erfundene Datei/Zeile/String-
Angaben — deterministisch und ohne zusätzliche Modellkosten.
"""
from __future__ import annotations

import re

from .plan_schema import Finding
from .tools import CodeTools

_WS = re.compile(r"\s+")
_LINE_TOLERANCE = 0          # Zeilennummer gilt als „korrekt genug" — wir korrigieren ohnehin


def _norm(s: str) -> str:
    """Whitespace-normalisiert für robusten Vergleich (Einrückung/Tabs egal)."""
    return _WS.sub(" ", s or "").strip()


def _find_line(haystack_lines: list[str], needle: str) -> int | None:
    """Erste 1-basierte Zeile, deren normalisierter Inhalt das Zitat enthält.
    Bei mehrzeiligen Zitaten zählt die erste Zeile des Zitats."""
    n = _norm(needle)
    if not n:
        return None
    # Mehrzeiliges Zitat: nimm die erste nicht-leere Zeile als Anker.
    first = next((ln for ln in needle.splitlines() if _norm(ln)), needle)
    anchor = _norm(first)
    for i, raw in enumerate(haystack_lines, 1):
        norm_line = _norm(raw)
        if anchor and anchor in norm_line:
            return i
    # Fallback: ganzes Zitat als ein Block irgendwo enthalten?
    joined = _norm("\n".join(haystack_lines))
    return 1 if n in joined else None


def verify_findings(
    findings: list[Finding],
    root: str,
) -> tuple[list[Finding], int]:
    """Prüft jeden Befund gegen die Quelle. Liefert (bestätigte_Befunde, verworfen).

    - Zitat in der Datei gefunden  → confidence='confirmed', Zeile auf die echte
      Fundstelle korrigiert.
    - Zitat nicht gefunden / Datei fehlt → verworfen (halluziniert).
    - Befund ohne Zitat → bleibt erhalten, aber confidence='unconfirmed'
      (kann nicht maschinell belegt werden, also ehrlich kennzeichnen).
    """
    tools = CodeTools(root)
    kept: list[Finding] = []
    dropped = 0
    cache: dict[str, list[str] | None] = {}

    for f in findings:
        quote = (f.code_quote or "").strip()
        if not quote:
            # Kein maschinell prüfbarer Beleg → behalten, aber ehrlich markieren.
            f.confidence = "unconfirmed"
            kept.append(f)
            continue

        path = (f.file or "").strip()
        if path not in cache:
            content = tools.read_raw(path)
            cache[path] = content.splitlines() if content is not None else None
        lines = cache[path]
        if lines is None:
            dropped += 1            # Datei existiert nicht / außerhalb Sandbox → halluziniert
            continue

        real_line = _find_line(lines, quote)
        if real_line is None:
            dropped += 1            # Zitat steht nirgends in der Datei → halluziniert
            continue

        f.line = real_line
        f.confidence = "confirmed"
        kept.append(f)

    return kept, dropped
