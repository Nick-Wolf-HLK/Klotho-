"""Agentischer Subagent: durchsucht einen Projektordner SELBST mit read-only
Werkzeugen (function-calling) und liefert am Ende einen Report.

Im Gegensatz zum einfachen Subagenten (dem der Code serviert wird) navigiert
dieser Agent eigenständig: list_dir → grep/find → read_file → … → finaler Report.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Optional

from . import i18n
from .llm_client import LLMClient
from .config import SubagentConfig
from .plan_schema import Finding, SubagentResponse
from .tools import TOOL_SCHEMAS, CodeTools

AGENT_SYSTEM = (
    "You are a senior code-audit subagent with READ-ONLY tools (list_dir, read_file, grep, "
    "find_files). read_file each source file in full and analyse it; grep only for targeted "
    "look-ups.\n"
    "MANAGED MEMORY: raw file content is dropped from your context a few steps after you read it. "
    "So immediately after each read_file, write a 1-2 sentence note with the file path, line "
    "number and the EXACT offending line copied verbatim. Rely on your notes, not the raw text.\n"
    "CHECK EVERY FILE for all of: (1) security — injection, auth gaps, secrets, path traversal, "
    "unsafe eval/deserialization, weak crypto; (2) concurrency — races, missing locks/await, "
    "blocking calls in async; (3) error handling — unhandled/swallowed exceptions, missing "
    "None/empty checks; (4) resources — leaks, missing cleanup, unbounded memory/input, "
    "quadratic loops; (5) validation — unvalidated input, off-by-one, boundary/overflow, bad "
    "indexes; (6) logic — inverted conditions, wrong operators, contract violations, dead code.\n"
    "FINAL OUTPUT: when done, reply with a SINGLE JSON object and nothing else (no prose/fences):\n"
    '{"findings":[{"file":"rel/path.py","line":42,"severity":"high","category":"bug",'
    '"issue":"what is wrong + impact","code_quote":"EXACT source line, verbatim","fix":"concrete fix"}]}\n'
    "RULES: code_quote MUST be a character-for-character copy of a line you actually read — a "
    "program checks it against the real source and DELETES findings that don't match; never "
    "reconstruct from memory. Use the real file path and line number. Report only genuine "
    'problems; {"findings":[]} is a valid honest answer — padding with weak/invented findings '
    "hurts your score.\n"
    "SEVERITY (be conservative, when unsure pick lower): critical = exploitable hole or guaranteed "
    "crash/data-loss on normal input, exact trigger nameable; high = clearly breaks a real feature "
    "or serious weakness; medium = edge-condition bug or real risk; low = minor quality. Only "
    "critical/high if you can point to the concrete mechanism in the quoted code."
)

MAX_ITERATIONS = 60
MAX_TOOL_RESULT_CHARS = 5000
KEEP_RAW_RESULTS = 3  # so viele jüngste Tool-Ergebnisse bleiben voll im Kontext (Rest evicted)
_EVICTED = "[Roh-Inhalt verworfen, um Kontext zu sparen — bereits gelesen; verlasse dich auf deine Notizen.]"


_SEVERITIES = {"critical", "high", "medium", "low"}
_CATEGORIES = {"bug", "logic", "quality", "security"}


def _extract_json(text: str) -> str | None:
    """Holt das äußerste JSON-Objekt aus einer Modellantwort (auch wenn das
    Modell Code-Fences oder etwas Prosa drumherum gesetzt hat)."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]


def parse_findings(content: str) -> list[Finding] | None:
    """Parst die JSON-Befundliste der finalen Agent-Antwort. None, wenn das
    Modell kein verwertbares JSON geliefert hat (→ Prosa-Fallback)."""
    blob = _extract_json(content)
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    raw = data.get("findings") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return None
    out: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sev = str(item.get("severity", "low")).lower().strip()
        cat = str(item.get("category", "quality")).lower().strip()
        try:
            line = int(item.get("line", 0) or 0)
        except (TypeError, ValueError):
            line = 0
        out.append(Finding(
            file=str(item.get("file", "")).strip(),
            line=line,
            severity=sev if sev in _SEVERITIES else "low",
            category=cat if cat in _CATEGORIES else "quality",
            issue=str(item.get("issue", "")).strip(),
            code_quote=str(item.get("code_quote", "")).strip(),
            fix=str(item.get("fix", "")).strip(),
        ))
    return out


def render_findings_text(findings: list[Finding]) -> str:
    """Lesbare Kurzfassung der Befunde — für die Judge-Bewertung und die Vorschau."""
    if not findings:
        return i18n.t("Keine Befunde.", "No findings.")
    lines = []
    for f in findings:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        lines.append(f"[{f.severity}/{f.category}] {loc} — {f.issue}")
    return "\n".join(lines)


def _clean_assistant(msg: dict) -> dict:
    """Assistant-Message ins erwartete Format bringen (content nie null)."""
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def _activity(name: str, args: dict) -> str:
    """Kurzbeschreibung der aktuellen Werkzeug-Aktion für die Live-Anzeige."""
    if name == "read_file":
        return f"liest {args.get('path', '?')}"
    if name == "grep":
        return f"grep „{args.get('pattern', '')}\""
    if name == "list_dir":
        return f"listet {args.get('path', '.')}"
    if name == "find_files":
        return f"sucht {args.get('glob', '*')}"
    return name


def _evict_old_tool_results(messages: list[dict]) -> None:
    """Managed memory: behält nur die jüngsten KEEP_RAW_RESULTS rohen Tool-
    Ergebnisse, ältere werden durch einen Platzhalter ersetzt. Die Notizen des
    Modells (assistant-Messages) bleiben vollständig erhalten — so kann ein Agent
    beliebig viele Dateien durchgehen, ohne den Kontext zu sprengen."""
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    if len(tool_msgs) <= KEEP_RAW_RESULTS:
        return
    for m in tool_msgs[:-KEEP_RAW_RESULTS]:
        if m.get("content") != _EVICTED:
            m["content"] = _EVICTED


def _coverage_directive(prompt: str, lens: Optional[str], assigned_files: Optional[list[str]]) -> str:
    """Hängt Lens-Fokus und Pflicht-Dateiliste an den Auftrag des Agenten an."""
    out = prompt
    if lens:
        out += (
            f"\n\nFOCUS FOR THIS PASS — look specifically for: {lens} "
            "Still report any other serious bug you happen to notice, but spend your "
            "attention on this focus."
        )
    if assigned_files:
        listing = "\n".join(f"- {f}" for f in assigned_files)
        out += (
            "\n\nASSIGNED FILES — read EVERY one of these with read_file and analyze it, then "
            "finish. Do NOT call find_files or list_dir — you already have your file list below; "
            "those calls only waste effort. read_file other files ONLY when strictly needed to "
            "confirm a real finding (e.g. trace a call). Coverage of THESE files is your duty:\n"
            f"{listing}"
        )
    return out


async def run_agentic_subagent(
    client: LLMClient,
    sub: SubagentConfig,
    prompt: str,
    root: str,
    *,
    max_iterations: int = MAX_ITERATIONS,
    timeout: Optional[float] = None,
    progress: Optional[Callable[[str, int, int], None]] = None,
    lens: Optional[str] = None,
    assigned_files: Optional[list[str]] = None,
    read_sink: Optional[set[str]] = None,
) -> SubagentResponse:
    tools = CodeTools(root)
    messages: list[dict] = [
        {"role": "system", "content": AGENT_SYSTEM + " " + i18n.output_directive()},
        {"role": "user", "content": _coverage_directive(prompt, lens, assigned_files)},
    ]
    start = time.perf_counter()
    files_read: set[str] = set()
    total_calls = 0

    def _elapsed() -> int:
        return int((time.perf_counter() - start) * 1000)

    def _footer(hit_limit: bool) -> str:
        note = (
            f"\n\n---\n_Untersucht: {len(files_read)} Dateien gelesen, "
            f"{total_calls} Werkzeug-Aufrufe in {_elapsed() // 1000}s."
        )
        if hit_limit:
            note += " ⚠️ Schritt-Limit erreicht — evtl. nicht der ganze Code abgedeckt."
        return note + "_"

    try:
        for _ in range(max_iterations):
            coro = client.chat_with_tools(sub.model, messages, TOOL_SCHEMAS)
            msg = await (asyncio.wait_for(coro, timeout) if timeout else coro)
            messages.append(_clean_assistant(msg))
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:  # Modell ist fertig → finaler Report
                content = (msg.get("content") or "").strip()
                if not content:
                    return SubagentResponse(
                        agent=sub.name, model=sub.model, response="",
                        elapsed_ms=_elapsed(),
                        error="leere Antwort vom Modell",
                    )
                findings = parse_findings(content)
                response_text = (
                    render_findings_text(findings) if findings is not None else content
                )
                return SubagentResponse(
                    agent=sub.name, model=sub.model,
                    response=response_text + _footer(hit_limit=False),
                    elapsed_ms=_elapsed(),
                    findings=findings or [],
                )

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                total_calls += 1
                if name == "read_file" and args.get("path"):
                    files_read.add(args["path"])
                    if read_sink is not None:
                        read_sink.add(args["path"])
                if progress:
                    progress(_activity(name, args), len(files_read), total_calls)
                result = tools.dispatch(name, args)[:MAX_TOOL_RESULT_CHARS]
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

            # Managed memory: alte Roh-Inhalte verwerfen (Notizen bleiben)
            _evict_old_tool_results(messages)

        # Iterationslimit erreicht → letzten Report einfordern (ohne Tools)
        messages.append({
            "role": "user",
            "content": "Step limit reached. Output your final findings NOW based on what you have "
                       "examined — as the single JSON object {\"findings\": [...]} specified above, "
                       "with no further tool calls and no prose.",
        })
        result = await client.chat(sub.model, messages, temperature=0.3)
        text = (result.text or "").strip()
        findings = parse_findings(text) if text else None
        response_text = render_findings_text(findings) if findings is not None else text
        return SubagentResponse(
            agent=sub.name, model=sub.model,
            response=(response_text + _footer(hit_limit=True)) if text else "",
            elapsed_ms=_elapsed(),
            error=None if text else "kein Report nach Schritt-Limit",
            findings=findings or [],
        )

    except asyncio.TimeoutError:
        return SubagentResponse(
            agent=sub.name, model=sub.model, response="",
            elapsed_ms=_elapsed(), error=f"Timeout nach {_elapsed()} ms",
        )
    except Exception as exc:
        return SubagentResponse(
            agent=sub.name, model=sub.model, response="",
            elapsed_ms=_elapsed(), error=str(exc),
        )
