"""Schlanke Zweisprachigkeit (Deutsch/Englisch) für Klotho.

Statt eines Key-Katalogs nutzen wir inline ``t("deutsch", "english")`` — beide
Varianten stehen direkt am Verwendungsort, das ist gut pflegbar. Die gewählte
Sprache steuert sowohl die UI-Texte als auch (über ``output_directive``) die
Sprache der LLM-generierten Inhalte (Reports, Bug-Reports, Pläne).
"""
from __future__ import annotations

_LANG = "de"


def set_language(lang: str) -> None:
    global _LANG
    _LANG = "en" if (lang or "").lower().startswith("en") else "de"


def get_language() -> str:
    return _LANG


def t(de: str, en: str) -> str:
    """Gibt den deutschen oder englischen Text gemäß gewählter Sprache."""
    return en if _LANG == "en" else de


def output_directive() -> str:
    """Anweisung an die LLMs, in welcher Sprache sie antworten sollen."""
    return (
        "Write your entire response in English."
        if _LANG == "en"
        else "Schreibe deine gesamte Antwort auf Deutsch."
    )
