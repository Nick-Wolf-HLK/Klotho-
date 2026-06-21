"""Konsolidiert die unabhängigen Audit-Reports der Subagenten zu EINEM
Bug-Report (Markdown) — dedupliziert, nach Schweregrad priorisiert, gewichtet
nach Judge-Score. Anders als der Synthesizer baut dies KEINEN Schritt-Plan,
sondern einen fertigen Befund-Report zum Weitergeben oder Fixen.
"""
from __future__ import annotations

from . import i18n, verify
from .llm_client import LLMClient
from .plan_schema import Finding, JudgeReport, SubagentResponse

_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_SEV_LABEL = {
    "critical": ("🔴", "Kritisch", "Critical"),
    "high": ("🟠", "Hoch", "High"),
    "medium": ("🟡", "Mittel", "Medium"),
    "low": ("🔵", "Niedrig", "Low"),
}


def _merge_findings(
    responses: list[SubagentResponse],
    report: JudgeReport,
    root: str,
) -> tuple[list[Finding], dict[str, int], int]:
    """Sammelt, verifiziert und dedupliziert die strukturierten Befunde aller
    Auditoren. Liefert (verifizierte_dedup_Befunde, {Auditor: #Befunde}, #verworfen).

    Dedup nach (Datei, korrigierte Zeile): Konsens mehrerer Auditoren erhöht die
    Sicherheit; bei Konflikt gewinnt der höchste Schweregrad und der Text des
    am höchsten gewichteten Auditors."""
    weight_map = {v.agent: v.weight for v in report.verdicts}
    per_auditor: dict[str, int] = {}
    dropped_total = 0
    # key -> (best_finding, best_weight, auditors:set)
    merged: dict[tuple[str, int], tuple[Finding, float, set[str]]] = {}

    for r in responses:
        if not r.findings:
            continue
        verified, dropped = verify.verify_findings(r.findings, root)
        dropped_total += dropped
        per_auditor[r.agent] = len(verified)
        w = weight_map.get(r.agent, 0.0)
        for f in verified:
            key = (f.file, f.line)
            if key not in merged:
                merged[key] = (f, w, {r.agent})
                continue
            best, best_w, auditors = merged[key]
            auditors.add(r.agent)
            # höchster Schweregrad gewinnt; bei Gleichstand der schwerere Text
            if _SEV_RANK.get(f.severity, 3) < _SEV_RANK.get(best.severity, 3):
                merged[key] = (f, max(best_w, w), auditors)
            elif w > best_w:
                merged[key] = (f, w, auditors)
            else:
                merged[key] = (best, best_w, auditors)

    findings: list[Finding] = []
    for f, _w, auditors in merged.values():
        # Konsens mehrerer Auditoren ⇒ bestätigt; sonst bleibt verify-Status.
        if len(auditors) >= 2:
            f.confidence = "confirmed"
        findings.append(f)
    findings.sort(key=lambda x: (_SEV_RANK.get(x.severity, 3), x.file, x.line))
    return findings, per_auditor, dropped_total


def render_bug_report(
    findings: list[Finding],
    per_auditor: dict[str, int],
    dropped: int,
) -> str:
    """Deterministischer Markdown-Bug-Report aus den verifizierten Befunden —
    ohne weiteres LLM, daher keine Re-Halluzination."""
    de = i18n.get_language() == "de"
    counts = {s: 0 for s in _SEV_RANK}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    title = "# Bug-Report" if de else "# Bug Report"
    lines = [title, ""]
    if de:
        lines.append(
            f"**{len(findings)}** verifizierte Befunde "
            f"(🔴 {counts['critical']} · 🟠 {counts['high']} · "
            f"🟡 {counts['medium']} · 🔵 {counts['low']}). "
            f"Jeder Befund wurde gegen den echten Quellcode geprüft; "
            f"{dropped} unbelegte/halluzinierte Behauptung(en) wurden verworfen."
        )
    else:
        lines.append(
            f"**{len(findings)}** verified findings "
            f"(🔴 {counts['critical']} · 🟠 {counts['high']} · "
            f"🟡 {counts['medium']} · 🔵 {counts['low']}). "
            f"Every finding was checked against the real source; "
            f"{dropped} unbacked/hallucinated claim(s) were dropped."
        )
    lines.append("")

    if not findings:
        lines.append(
            "_Keine gegen den Quellcode belegbaren Befunde._" if de
            else "_No findings could be backed against the source._"
        )
    cat_label = {
        "bug": "Bug", "logic": "Logikfehler" if de else "Logic",
        "quality": "Qualität" if de else "Quality",
        "security": "Sicherheit" if de else "Security",
    }
    current_sev = None
    for f in findings:
        emoji, de_name, en_name = _SEV_LABEL.get(f.severity, ("🔵", "Niedrig", "Low"))
        if f.severity != current_sev:
            current_sev = f.severity
            lines.append(f"## {emoji} {de_name if de else en_name}")
            lines.append("")
        unconf = ""
        if f.confidence != "confirmed":
            unconf = " _(unbestätigt — verifizieren)_" if de else " _(unconfirmed — verify)_"
        lines.append(f"### {f.issue or (f.file)}{unconf}")
        loc_label = "Datei:Zeile" if de else "File:Line"
        lines.append(f"- **{loc_label}:** `{f.file}:{f.line}`")
        lines.append(f"- **{'Kategorie' if de else 'Category'}:** {cat_label.get(f.category, f.category)}")
        if f.code_quote:
            lines.append(f"- **{'Beleg' if de else 'Evidence'}:**")
            lines.append("  ```")
            for q in f.code_quote.splitlines() or [f.code_quote]:
                lines.append(f"  {q}")
            lines.append("  ```")
        if f.fix:
            lines.append(f"- **Fix:** {f.fix}")
        lines.append("")

    investigated = ", ".join(f"{a}: {n}" for a, n in per_auditor.items())
    if investigated:
        foot = (f"_Auditoren (verifizierte Befunde): {investigated}._" if de
                else f"_Auditors (verified findings): {investigated}._")
        lines.append("---")
        lines.append(foot)
    return "\n".join(lines).strip()


def build_bug_report(
    responses: list[SubagentResponse],
    report: JudgeReport,
    root: str,
) -> str:
    """End-to-End: verifizieren → dedup → deterministisch rendern. Leerer String,
    wenn KEIN Auditor strukturierte Befunde lieferte (dann LLM-Fallback nutzen)."""
    if not any(r.findings for r in responses):
        return ""
    findings, per_auditor, dropped = _merge_findings(responses, report, root)
    return render_bug_report(findings, per_auditor, dropped)


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
