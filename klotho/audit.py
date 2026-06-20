"""Konsolidiert die unabhängigen Audit-Reports der Subagenten zu EINEM
Bug-Report (Markdown) — dedupliziert, nach Schweregrad priorisiert, gewichtet
nach Judge-Score. Anders als der Synthesizer baut dies KEINEN Schritt-Plan,
sondern einen fertigen Befund-Report zum Weitergeben oder Fixen.
"""
from __future__ import annotations

from typing import Optional

from . import compress
from .compress import CompressionStats
from .llm_client import LLMClient
from .plan_schema import JudgeReport, SubagentResponse

AUDIT_SYNTH_SYSTEM = (
    "You merge several independent code-audit reports into ONE consolidated bug report. "
    "Deduplicate overlapping findings, keep the strongest evidence, trust higher-weighted "
    "auditors more, and order by severity. Output a clean report of CONCRETE FINDINGS — "
    "never a step-by-step plan of how to look for bugs. Drop any finding that no auditor "
    "backed with a real file path / line / code quote."
)


def _build_audit_prompt(
    original_prompt: str,
    responses: list[SubagentResponse],
    report: JudgeReport,
    compression: str,
    stats: Optional[CompressionStats],
) -> str:
    weight_map = {v.agent: v.weight for v in report.verdicts}
    blocks = []
    for r in responses:
        if r.error or not (r.response or "").strip():
            continue
        w = weight_map.get(r.agent, 0.0)
        body = compress.compress_text(r.response, compression, stats=stats)
        blocks.append(f"### Auditor: {r.agent} (weight={w:.2f})\n{body}")
    joined = "\n\n".join(blocks)
    return (
        f"AUDIT-AUFTRAG:\n{original_prompt}\n\n"
        f"UNABHÄNGIGE AUDIT-REPORTS:\n{joined}\n\n"
        "Erzeuge EINEN konsolidierten Bug-Report in deutschem Markdown, genau so:\n\n"
        "# Bug-Report\n\n"
        "Ein Satz Zusammenfassung + Zähltabelle der Befunde je Schweregrad.\n\n"
        "Dann je Befund, gruppiert nach Schweregrad in dieser Reihenfolge "
        "(🔴 Kritisch, 🟠 Hoch, 🟡 Mittel, 🔵 Niedrig):\n\n"
        "### 🔴 <Kurztitel>\n"
        "- **Datei:Zeile:** `pfad/datei.py:42`\n"
        "- **Kategorie:** Bug | Logikfehler | Qualität | Sicherheit\n"
        "- **Problem:** worin der Fehler besteht und was er bewirkt\n"
        "- **Beleg:** das zitierte Code-Stück (falls ein Auditor es geliefert hat)\n"
        "- **Fix:** konkreter Lösungsvorschlag (gern mit Code)\n\n"
        "Regeln: Nur Befunde mit konkreter Datei/Zeile aufnehmen. Erfinde NICHTS. "
        "Befunde ohne Code-Beleg oder mit Widerspruch zwischen Auditoren als "
        "'(unbestätigt — verifizieren)' kennzeichnen. Keine Schritt-für-Schritt-Anleitung."
    )


async def synthesize_bug_report(
    client: LLMClient,
    model: str,
    original_prompt: str,
    responses: list[SubagentResponse],
    report: JudgeReport,
    *,
    compression: str = "safe",
    stats: Optional[CompressionStats] = None,
) -> str:
    """Gibt den konsolidierten Bug-Report als Markdown-String zurück."""
    user_prompt = _build_audit_prompt(original_prompt, responses, report, compression, stats)
    result = await client.chat(
        model,
        [
            {"role": "system", "content": AUDIT_SYNTH_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    return (result.text or "").strip()
