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
    "You are a senior software-analysis subagent with READ-ONLY tools to explore a "
    "project folder: list_dir, read_file, grep, find_files.\n\n"
    "Work EFFICIENTLY and THOROUGHLY within your step budget:\n"
    "1. Call find_files ONCE to see the whole file list — don't re-list directories you've seen.\n"
    "2. Then READ the important source files in full with read_file. Prefer reading complete "
    "files over many small greps — you understand code far better in context.\n"
    "3. Use grep SPARINGLY, only for targeted look-ups.\n"
    "4. Spend MOST of your steps on read_file. Aim for BROAD coverage across the whole codebase.\n\n"
    "CRITICAL — MANAGED MEMORY: Your context is automatically trimmed to save space. After you "
    "read a file, the RAW file content will be removed from your context within a few steps. "
    "Therefore, IMMEDIATELY after each read_file, write a short note (1-3 sentences) capturing "
    "what you found in that file (file path, line numbers, the EXACT offending line copied "
    "verbatim) BEFORE moving on. Rely on your running notes, not on the raw text — it will be gone.\n\n"
    "=== WHAT TO LOOK FOR — run through ALL of these for every file ===\n"
    "1. Security: injection (SQL/command/template), auth/authorization gaps, secrets in code, path "
    "traversal, unsafe deserialization/eval, SSRF, weak crypto/hashing.\n"
    "2. Concurrency: race conditions, shared mutable state without locks, blocking calls in async "
    "code, missing await, deadlocks, non-atomic read-modify-write.\n"
    "3. Error handling: unhandled exceptions, swallowed/broad excepts, missing None/empty checks, "
    "unchecked return values, state left inconsistent on error.\n"
    "4. Resources & performance: leaked files/sockets/connections, missing cleanup, unbounded "
    "memory or input (OOM), quadratic/N+1 loops, missing size limits.\n"
    "5. Input validation & boundaries: unvalidated external input, off-by-one, empty/overflow "
    "conditions, type confusion, unchecked indexes/keys.\n"
    "6. Logic correctness: inverted/incorrect conditions, wrong operators, mismatched units, "
    "contract violations, dead/unreachable code, copy-paste mistakes.\n\n"
    "=== FINAL OUTPUT — STRICT ===\n"
    "When finished, your final message (NO further tool calls) MUST be a SINGLE JSON object and "
    "nothing else — no prose, no markdown fences:\n"
    '{"findings": [\n'
    '  {"file": "relative/path.py", "line": 42, "severity": "high", "category": "bug",\n'
    '   "issue": "what is wrong and its concrete impact",\n'
    '   "code_quote": "the EXACT source line(s), copied verbatim — do NOT paraphrase",\n'
    '   "fix": "concrete fix"}\n'
    "]}\n\n"
    "HARD RULES:\n"
    "- `code_quote` MUST be an exact, character-for-character copy of a line you actually read. "
    "It is checked against the real source by a program; if it does not match, your finding is "
    "DELETED and you get no credit. Never reconstruct a line from memory.\n"
    "- `file` must be the real relative path; `line` the real line number.\n"
    "- Report ONLY genuine problems. An empty list {\"findings\": []} is a valid, honest answer — "
    "padding the list with weak or invented findings hurts your score.\n\n"
    "SEVERITY RUBRIC — be conservative, when in doubt pick the LOWER level:\n"
    "- critical: an exploitable security hole OR a guaranteed crash / data loss on normal input. "
    "You must be able to name the exact trigger from code you read.\n"
    "- high: a clear bug that breaks a real feature for realistic input, or a serious security weakness.\n"
    "- medium: a bug only under edge conditions, or a real correctness/robustness risk.\n"
    "- low: minor quality, style, or maintainability issue.\n"
    "Do NOT label something critical/high unless you can point to the concrete mechanism in the quoted code."
)

MAX_ITERATIONS = 60
MAX_TOOL_RESULT_CHARS = 8000
KEEP_RAW_RESULTS = 6  # so viele jüngste Tool-Ergebnisse bleiben voll im Kontext
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
            "\n\nASSIGNED FILES — you MUST read EVERY one of these with read_file and "
            "analyze it before you finish. Do not skip any; broad coverage of THESE files "
            "is your primary duty. You may additionally read other files to trace data flow:\n"
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
