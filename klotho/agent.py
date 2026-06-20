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

from .llm_client import LLMClient
from .config import SubagentConfig
from .plan_schema import SubagentResponse
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
    "what you found in that file (file path, line numbers, the issue) BEFORE moving on. Rely on "
    "your running notes, not on the raw text — it will be gone. This lets you cover hundreds of "
    "files without running out of context.\n\n"
    "Reference exact file paths and line numbers in every finding. When finished, compile all your "
    "notes into a final report as a normal assistant message with NO further tool calls.\n\n"
    "If the task is to find bugs / logic errors / quality issues, report each finding as:\n"
    "- file:line  · severity (critical/high/medium/low) · category (bug/logic/quality/security)\n"
    "- what is wrong and its impact\n"
    "- a SHORT QUOTE of the actual offending code as evidence\n"
    "- a concrete fix\n"
    "NEVER invent a finding. Every finding MUST be backed by code you actually read with read_file. "
    "If you are not certain, label it '(unconfirmed)'. Quote the real line — do not paraphrase from memory."
)

MAX_ITERATIONS = 60
MAX_TOOL_RESULT_CHARS = 8000
KEEP_RAW_RESULTS = 6  # so viele jüngste Tool-Ergebnisse bleiben voll im Kontext
_EVICTED = "[Roh-Inhalt verworfen, um Kontext zu sparen — bereits gelesen; verlasse dich auf deine Notizen.]"


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


async def run_agentic_subagent(
    client: LLMClient,
    sub: SubagentConfig,
    prompt: str,
    root: str,
    *,
    max_iterations: int = MAX_ITERATIONS,
    timeout: Optional[float] = None,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> SubagentResponse:
    tools = CodeTools(root)
    messages: list[dict] = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": prompt},
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
                return SubagentResponse(
                    agent=sub.name, model=sub.model,
                    response=content + _footer(hit_limit=False),
                    elapsed_ms=_elapsed(),
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
            "content": "Schritt-Limit erreicht. Gib JETZT deinen finalen Report "
                       "basierend auf dem bisher Untersuchten — ohne weitere Werkzeuge.",
        })
        result = await client.chat(sub.model, messages, temperature=0.3)
        text = (result.text or "").strip()
        return SubagentResponse(
            agent=sub.name, model=sub.model,
            response=(text + _footer(hit_limit=True)) if text else "",
            elapsed_ms=_elapsed(),
            error=None if text else "kein Report nach Schritt-Limit",
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
