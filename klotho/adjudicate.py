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
from .agent import _clean_assistant, _extract_json
from .llm_client import LLMClient
from .plan_schema import Finding
from .tools import TOOL_SCHEMAS, CodeTools

ADJUDICATOR_SYSTEM = (
    "You are a SKEPTICAL senior code reviewer double-checking ONE bug finding produced by an "
    "automated auditor. Automated auditors produce many FALSE POSITIVES: they claim something is "
    "'missing' that actually exists in another module, misread the direction of a check, miss a "
    "mitigation that runs elsewhere (e.g. a default secret replaced at startup), or state things "
    "that are simply factually wrong. Assume the claim is GUILTY-until-proven and verify it against "
    "the REAL code.\n\n"
    "You have read-only tools: list_dir, read_file, grep, find_files. USE them. Crucially, TRACE "
    "ACROSS FILES when the claim depends on it: if a default value is flagged, grep for where that "
    "symbol is set/used; if a field is 'missing', search the config/model modules; if a check looks "
    "wrong, read it slowly and reason about it concretely.\n\n"
    "Then decide:\n"
    "- refuted: the claim is wrong — the field exists, the check is correct, a mitigation handles it, "
    "or the statement is factually false. (This finding will be DROPPED.)\n"
    "- confirmed: you verified by reading the code that the bug is real.\n"
    "- uncertain: plausible but you cannot confirm it from the code.\n\n"
    "RE-RATE severity conservatively (downgrade anything mitigated elsewhere). critical = exploitable "
    "security hole or guaranteed crash/data-loss on normal input, with a concrete trigger you can "
    "name; high = clearly breaks a real feature / serious weakness; medium = edge-condition bug or "
    "real risk; low = minor quality. Only confirm critical/high if you can point to the exact "
    "mechanism in code you read.\n\n"
    "Your FINAL message must be a SINGLE JSON object and nothing else (no prose, no fences):\n"
    '{"verdict":"confirmed|refuted|uncertain","severity":"critical|high|medium|low",'
    '"reason":"one or two sentences citing exactly what you read"}'
)

MAX_ITERATIONS = 8
MAX_TOOL_RESULT_CHARS = 8000
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
        f"Current content of {f.file} (line-numbered; read other files as needed to trace the claim):\n"
        f"{prefill}"
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
    """Liefert (Befund_aktualisiert, behalten?). Bei Fehler/kein-Urteil: behalten."""
    tools = CodeTools(root)
    messages = [
        {"role": "system", "content": ADJUDICATOR_SYSTEM + " " + i18n.output_directive()},
        {"role": "user", "content": _finding_prompt(tools, f)},
    ]
    try:
        for _ in range(MAX_ITERATIONS):
            coro = client.chat_with_tools(model, messages, TOOL_SCHEMAS)
            msg = await (asyncio.wait_for(coro, timeout) if timeout else coro)
            messages.append(_clean_assistant(msg))
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                verdict = _parse_verdict(msg.get("content") or "")
                break
            for tc in tool_calls:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                result = tools.dispatch(fn.get("name", ""), args)[:MAX_TOOL_RESULT_CHARS]
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})
        else:
            verdict = None  # Iterationslimit ohne finales Urteil
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
