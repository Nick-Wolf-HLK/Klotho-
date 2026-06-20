"""Konsolidiert die unabhängigen Audit-Reports der Subagenten zu EINEM
Bug-Report (Markdown) — dedupliziert, nach Schweregrad priorisiert, gewichtet
nach Judge-Score. Anders als der Synthesizer baut dies KEINEN Schritt-Plan,
sondern einen fertigen Befund-Report zum Weitergeben oder Fixen.
"""
from __future__ import annotations

from typing import Optional

from . import i18n
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
) -> str:
    weight_map = {v.agent: v.weight for v in report.verdicts}
    blocks = []
    for r in responses:
        if r.error or not (r.response or "").strip():
            continue
        w = weight_map.get(r.agent, 0.0)
        blocks.append(f"### Auditor: {r.agent} (weight={w:.2f})\n{r.response}")
    joined = "\n\n".join(blocks)

    fmt = i18n.t(
        de=(
            "Erzeuge EINEN konsolidierten Bug-Report in deutschem Markdown, genau so:\n\n"
            "# Bug-Report\n\n"
            "Ein Satz Zusammenfassung + Zähltabelle der Befunde je Schweregrad.\n\n"
            "Dann je Befund, gruppiert nach Schweregrad (🔴 Kritisch, 🟠 Hoch, 🟡 Mittel, 🔵 Niedrig):\n\n"
            "### 🔴 <Kurztitel>\n"
            "- **Datei:Zeile:** `pfad/datei.py:42`\n"
            "- **Kategorie:** Bug | Logikfehler | Qualität | Sicherheit\n"
            "- **Problem:** worin der Fehler besteht und was er bewirkt\n"
            "- **Beleg:** das zitierte Code-Stück (falls ein Auditor es geliefert hat)\n"
            "- **Fix:** konkreter Lösungsvorschlag (gern mit Code)\n\n"
            "Regeln: Nur Befunde mit konkreter Datei/Zeile. Erfinde NICHTS. Befunde ohne "
            "Code-Beleg oder mit Widerspruch als '(unbestätigt — verifizieren)' kennzeichnen. "
            "Keine Schritt-für-Schritt-Anleitung."
        ),
        en=(
            "Produce ONE consolidated bug report in English Markdown, exactly like this:\n\n"
            "# Bug Report\n\n"
            "One-sentence summary + a count table of findings per severity.\n\n"
            "Then each finding, grouped by severity (🔴 Critical, 🟠 High, 🟡 Medium, 🔵 Low):\n\n"
            "### 🔴 <short title>\n"
            "- **File:Line:** `path/file.py:42`\n"
            "- **Category:** Bug | Logic | Quality | Security\n"
            "- **Problem:** what is wrong and its impact\n"
            "- **Evidence:** the quoted code snippet (if an auditor provided one)\n"
            "- **Fix:** concrete suggested fix (code welcome)\n\n"
            "Rules: only findings with a concrete file/line. Invent NOTHING. Mark findings "
            "without code evidence or with disagreement as '(unconfirmed — verify)'. "
            "No step-by-step plan."
        ),
    )
    return (
        f"AUDIT TASK:\n{original_prompt}\n\n"
        f"INDEPENDENT AUDIT REPORTS:\n{joined}\n\n{fmt}"
    )


async def synthesize_bug_report(
    client: LLMClient,
    model: str,
    original_prompt: str,
    responses: list[SubagentResponse],
    report: JudgeReport,
) -> str:
    """Gibt den konsolidierten Bug-Report als Markdown-String zurück."""
    user_prompt = _build_audit_prompt(original_prompt, responses, report)
    result = await client.chat(
        model,
        [
            {"role": "system", "content": AUDIT_SYNTH_SYSTEM + " " + i18n.output_directive()},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    return (result.text or "").strip()
