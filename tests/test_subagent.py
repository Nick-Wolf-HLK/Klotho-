"""Tests for subagent.run_subagents_parallel with a fake LLMClient."""
import asyncio
from typing import Any

from klotho.config import SubagentConfig
from klotho.llm_client import LLMResult
from klotho.subagent import run_subagents_parallel


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []

    async def chat(self, model: str, messages: list[dict], **kw: Any) -> LLMResult:
        self.calls.append((model, messages))
        return LLMResult(text=f"plan from {model}", elapsed_ms=10, raw={})


def test_run_subagents_parallel():
    client = FakeClient()
    subs = [
        SubagentConfig("a", "m1", 1),
        SubagentConfig("b", "m2", 2),
        SubagentConfig("c", "m3", 3),
    ]
    results = asyncio.run(run_subagents_parallel(client, subs, "do thing"))
    assert len(results) == 3
    agents = {r.agent for r in results}
    assert agents == {"a", "b", "c"}
    assert all(r.error is None for r in results)
    assert len(client.calls) == 3


def test_run_subagents_parallel_preserves_order():
    client = FakeClient()
    subs = [
        SubagentConfig("z", "mz", 5),
        SubagentConfig("a", "ma", 1),
    ]
    results = asyncio.run(run_subagents_parallel(client, subs, "x"))
    assert results[0].agent == "a"
    assert results[1].agent == "z"