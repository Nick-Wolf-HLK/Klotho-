"""Tests für die automatische Rollen-/Modellauswahl."""
import asyncio
import json

from klotho import model_select as ms


AVAILABLE = [
    "gpt-oss:20b", "gpt-oss:120b-cloud", "gemma4:31b-cloud", "qwen3.5:27b",
    "deepseek-v4-pro:cloud", "deepseek-coder:cloud", "qwq:32b", "glm-5.2:cloud",
]


def test_is_reasoning_flags_pro_and_thinking_models():
    assert ms.is_reasoning("deepseek-v4-pro:cloud")
    assert ms.is_reasoning("qwq:32b")
    assert not ms.is_reasoning("gpt-oss:20b")
    assert not ms.is_reasoning("gemma4:31b-cloud")


def test_is_reasoning_coder_exception():
    # deepseek-coder ist KEIN Reasoning-Modell trotz "deepseek"
    assert not ms.is_reasoning("deepseek-coder:cloud")


def test_heuristic_avoids_reasoning_in_subagents():
    choice = ms.heuristic_select(AVAILABLE)
    assert choice["subagents"]
    assert not any(ms.is_reasoning(m) for m in choice["subagents"])
    assert "deepseek-v4-pro:cloud" not in choice["subagents"]
    assert "qwq:32b" not in choice["subagents"]


def test_heuristic_judge_prefers_gpt_oss_20b():
    choice = ms.heuristic_select(AVAILABLE)
    assert choice["judge"] == "gpt-oss:20b"


def test_heuristic_empty():
    choice = ms.heuristic_select([])
    assert choice["subagents"] == []


class _R:
    def __init__(self, text): self.text = text


class _FakeClient:
    def __init__(self, text): self._t = text
    async def chat(self, model, messages, *, temperature=0.1):
        return _R(self._t)


def _run(client):
    return asyncio.run(ms.select_models(client, "sel", AVAILABLE, task="code audit"))


def test_select_models_uses_valid_llm_choice():
    payload = json.dumps({
        "orchestrator": "glm-5.2:cloud", "judge": "gpt-oss:20b",
        "subagents": ["gpt-oss:120b-cloud", "qwen3.5:27b"], "reason": "efficient",
    })
    choice = _run(_FakeClient(payload))
    assert choice["orchestrator"] == "glm-5.2:cloud"
    assert choice["subagents"] == ["gpt-oss:120b-cloud", "qwen3.5:27b"]
    assert choice["reason"] == "efficient"


def test_select_models_falls_back_on_invalid_ids():
    payload = json.dumps({
        "orchestrator": "nonexistent:cloud", "judge": "also-fake",
        "subagents": ["nope"], "reason": "x",
    })
    choice = _run(_FakeClient(payload))
    # ungültige ids → Heuristik
    assert choice["orchestrator"] in AVAILABLE
    assert all(m in AVAILABLE for m in choice["subagents"])


def test_select_models_falls_back_on_garbage():
    choice = _run(_FakeClient("no json here"))
    assert choice["orchestrator"] in AVAILABLE
    assert choice["subagents"]
