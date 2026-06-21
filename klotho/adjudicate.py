"""Adversariale Verifikation der Befunde — die zweite Stufe.

Die deterministische Quote-Prüfung (verify.py) stellt sicher, dass das zitierte
Code-Stück WIRKLICH existiert. Sie kann aber nicht beurteilen, ob die
SCHLUSSFOLGERUNG stimmt: „Feld fehlt" (existiert in anderem Modul), „Check
invertiert" (ist korrekt), „Default-Secret kritisch" (wird beim Start ersetzt),
„Base64 wird größer" (faktisch falsch). Hier prüft je Befund ein SKEPTISCHER
Reviewer den echten Code (mit denselben read-only Werkzeugen) und urteilt:
bestätigt / widerlegt / unsicher — und kalibriert den Schweregrad neu.

Widerlegte Befunde fliegen raus, mitigierte werden herabgestuft. Im Zweifel
bleibt ein Befund erhalten (nur klar widerlegte werden verworfen), damit echte
Funde nicht verloren gehen.
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional

from . import i18n
from .agent import _extract_json
from .llm_client import LLMClient
from .plan_schema import Finding
from .tools import CodeTools

ADJUDICATOR_SYSTEM = (
    "You are a SKEPTICAL senior code reviewer double-checking ONE bug finding from an automated "
    "auditor. Such auditors produce many FALSE POSITIVES: they claim something is 'missing' that "
    "exists, misread the direction of a check, miss a mitigation, or state things that are simply "
    "false. Assume the claim is GUILTY-until-proven and verify it against the file shown below.\n"
    "Decide: refuted = the claim is wrong (field exists, check is correct, mitigation handles it, "
    "or factually false) → DROP it; confirmed = the bug is real per the code; uncertain = plausible "
    "but not confirmable from what you see.\n"
    "Re-rate severity conservatively (downgrade anything mitigated). Only confirmed critical/high if "
    "you can point to the concrete mechanism in the shown code.\n"
    "Reply with a SINGLE JSON object, nothing else (no prose/fences):\n"
    '{"verdict":"confirmed|refuted|uncertain","severity":"critical|high|medium|low",'
    '"reason":"one short sentence"}'
)

PREFILL_CHARS = 6000
_VERDICTS = {"confirmed", "refuted", "uncertain"}
_SEVERITIES = {"critical", "high", "medium", "low"}


def _finding_prompt(tools: CodeTools, f: Finding) -> str:
    prefill = tools.read_file(f.file)[:PREFILL_CHARS]
    return (
        "Claimed finding to verify:\n"
        f"- file:line : {f.file}:{f.line}\n"
        f"- severity  : {f.severity}\n"
        f"- category  : {f.category}\n"
        f"- issue     : {f.issue}\n"
        f"- code_quote:\n{f.code_quote}\n"
        f"- proposed fix: {f.fix}\n\n"
        f"Source of {f.file} (line-numbered):\n{prefill}"
    )


def _parse_verdict(text: str) -> dict | None:
    blob = _extract_json(text)
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


async def _adjudicate_one(
    client: LLMClient,
    model: str,
    f: Finding,
    root: str,
    *,
    timeout: Optional[float],
) -> tuple[Finding, bool]:
    """EIN Einspeisungs-Call (kein Tool-Loop): die betroffene Datei ist im Prompt.
    Liefert (Befund_aktualisiert, behalten?). Bei Fehler/kein-Urteil: behalten."""
    tools = CodeTools(root)
    messages = [
        {"role": "system", "content": ADJUDICATOR_SYSTEM + " " + i18n.output_directive()},
        {"role": "user", "content": _finding_prompt(tools, f)},
    ]
    try:
        coro = client.chat(model, messages, temperature=0.1)
        result = await (asyncio.wait_for(coro, timeout) if timeout else coro)
        verdict = _parse_verdict(result.text or "")
    except Exception:
        return f, True  # Im Zweifel behalten — keine echten Funde verlieren.

    if not verdict:
        return f, True
    v = str(verdict.get("verdict", "")).lower().strip()
    if v == "refuted":
        return f, False
    sev = str(verdict.get("severity", f.severity)).lower().strip()
    if sev in _SEVERITIES:
        f.severity = sev
    f.confidence = "confirmed" if v == "confirmed" else "unconfirmed"
    return f, True


async def adjudicate_findings(
    client: LLMClient,
    model: str,
    findings: list[Finding],
    root: str,
    *,
    timeout: Optional[float] = None,
    max_concurrency: int = 4,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[list[Finding], int]:
    """Prüft alle Befunde adversarial (parallel, begrenzt). Liefert
    (behaltene_Befunde, #widerlegt)."""
    if not findings:
        return [], 0
    sem = asyncio.Semaphore(max_concurrency)
    done = 0
    total = len(findings)

    async def _guarded(f: Finding) -> tuple[Finding, bool]:
        nonlocal done
        async with sem:
            res = await _adjudicate_one(client, model, f, root, timeout=timeout)
        done += 1
        if on_progress:
            on_progress(done, total)
        return res

    results = await asyncio.gather(*(_guarded(f) for f in findings))
    kept = [f for f, keep in results if keep]
    refuted = sum(1 for _f, keep in results if not keep)
    return kept, refuted
