"""Agentischer Subagent: durchsucht einen Projektordner SELBST mit read-only
Werkzeugen (function-calling) und liefert am Ende einen Report.

Im Gegensatz zum einfachen Subagenten (dem der Code serviert wird) navigiert
dieser Agent eigenständig: list_dir → grep/find → read_file → … → finaler Report.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from .llm_client import LLMClient
from .config import SubagentConfig
from .plan_schema import SubagentResponse
from .tools import TOOL_SCHEMAS, CodeTools

AGENT_SYSTEM = (
    "You are a senior software-analysis subagent with READ-ONLY tools to explore a "
    "project folder: list_dir, read_file, grep, find_files. ACTUALLY inspect the real "
    "code before answering: start by listing/searching, then read the relevant files. "
    "Work systematically across the codebase, not just one file. Reference exact file "
    "paths and line numbers in your findings. When finished, output your final report "
    "as a normal assistant message with NO further tool calls. Be concrete; avoid filler."
)

MAX_ITERATIONS = 24
MAX_TOOL_RESULT_CHARS = 8000


def _clean_assistant(msg: dict) -> dict:
    """Assistant-Message ins erwartete Format bringen (content nie null)."""
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


async def run_agentic_subagent(
    client: LLMClient,
    sub: SubagentConfig,
    prompt: str,
    root: str,
    *,
    max_iterations: int = MAX_ITERATIONS,
    timeout: Optional[float] = None,
) -> SubagentResponse:
    tools = CodeTools(root)
    messages: list[dict] = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    start = time.perf_counter()

    def _elapsed() -> int:
        return int((time.perf_counter() - start) * 1000)

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
                    agent=sub.name, model=sub.model, response=content,
                    elapsed_ms=_elapsed(),
                )

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                result = tools.dispatch(name, args)[:MAX_TOOL_RESULT_CHARS]
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

        # Iterationslimit erreicht → letzten Report einfordern (ohne Tools)
        messages.append({
            "role": "user",
            "content": "Iterationslimit erreicht. Gib JETZT deinen finalen Report "
                       "basierend auf dem bisher Untersuchten — ohne weitere Werkzeuge.",
        })
        result = await client.chat(sub.model, messages, temperature=0.3)
        text = (result.text or "").strip()
        return SubagentResponse(
            agent=sub.name, model=sub.model, response=text,
            elapsed_ms=_elapsed(),
            error=None if text else "kein Report nach Iterationslimit",
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
