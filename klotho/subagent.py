"""Subagent: runs a single LLM with a prompt, returns structured response."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from .config import SubagentConfig
from .llm_client import LLMClient
from .plan_schema import SubagentResponse


SUBAGENT_SYSTEM = (
    "You are a senior planning subagent. Given a task prompt, produce a "
    "concrete, actionable plan with numbered steps. Be specific, pragmatic "
    "and complete. Avoid filler. Use markdown headings if helpful."
)

SUBAGENT_SYSTEM_WITH_CODE = (
    "You are a senior software analysis subagent. The relevant source code is "
    "embedded in the user prompt under '=== CODEBASE ==='. You have NO file "
    "system access and need NO tools — analyse ONLY the embedded code. Be "
    "concrete: reference exact file paths and (where visible) line context. "
    "Avoid filler. Use markdown headings."
)


async def run_subagent(
    client: LLMClient,
    sub: SubagentConfig,
    prompt: str,
    *,
    refine_prompt: Optional[str] = None,
    context: Optional[str] = None,
    timeout: Optional[float] = None,
) -> SubagentResponse:
    final_prompt = refine_prompt or prompt
    if context:
        system = SUBAGENT_SYSTEM_WITH_CODE
        final_prompt = f"{final_prompt}\n\n=== CODEBASE ===\n{context}"
    else:
        system = SUBAGENT_SYSTEM
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": final_prompt},
    ]
    start = time.perf_counter()
    try:
        coro = client.chat(sub.model, messages, temperature=0.7)
        if timeout is not None:
            result = await asyncio.wait_for(coro, timeout=timeout)
        else:
            result = await coro
        elapsed = int((time.perf_counter() - start) * 1000)
        return SubagentResponse(
            agent=sub.name,
            model=sub.model,
            response=result.text,
            elapsed_ms=elapsed,
        )
    except Exception as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return SubagentResponse(
            agent=sub.name,
            model=sub.model,
            response="",
            elapsed_ms=elapsed,
            error=str(exc),
        )


async def run_subagents_parallel(
    client: LLMClient,
    subagents: list[SubagentConfig],
    prompt: str,
    *,
    refine_prompt: Optional[str] = None,
    context: Optional[str] = None,
    timeout: Optional[float] = None,
) -> list[SubagentResponse]:
    tasks = [
        run_subagent(
            client, sub, prompt,
            refine_prompt=refine_prompt, context=context, timeout=timeout,
        )
        for sub in sorted(subagents, key=lambda s: s.order)
    ]
    return await asyncio.gather(*tasks)