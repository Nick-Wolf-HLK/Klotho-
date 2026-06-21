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
    # ohne Cloud-Bevorzugung wird das schlanke gpt-oss:20b als Judge gewählt
    choice = ms.heuristic_select(AVAILABLE, prefer_cloud=False)
    assert choice["judge"] == "gpt-oss:20b"


def test_heuristic_empty():
    choice = ms.heuristic_select([])
    assert choice["subagents"] == []


def test_prefer_cloud_excludes_local_when_enough_cloud():
    # genug Cloud-Modelle → lokale (gemma4:12b, gpt-oss:20b, qwen3.5:27b) fliegen raus
    choice = ms.heuristic_select(AVAILABLE)
    chosen = [choice["orchestrator"], choice["judge"], *choice["subagents"]]
    assert all(ms.is_cloud(m) for m in chosen), chosen


def test_falls_back_to_local_when_too_few_cloud():
    local_heavy = ["gemma4:12b", "gpt-oss:20b", "qwen3.5:27b", "glm-5.2:cloud"]
    choice = ms.heuristic_select(local_heavy)   # nur 1 Cloud → lokal erlaubt
    assert choice["subagents"]                  # nicht leer
    assert any(not ms.is_cloud(m) for m in choice["subagents"])


def test_is_cloud():
    assert ms.is_cloud("gpt-oss:120b-cloud")
    assert ms.is_cloud("minimax-m2.5:cloud")
    assert not ms.is_cloud("gemma4:12b")
    assert not ms.is_cloud("qwen3.5:27b")


class _R:
    def __init__(self, text): self.text = text


class _FakeClient:
    def __init__(self, text): self._t = text
    async def chat(self, model, messages, *, temperature=0.1):
        return _R(self._t)


def _run(client):
    return asyncio.run(ms.select_models(client, "sel", AVAILABLE, task="code audit"))


def test_select_models_uses_valid_llm_choice():
    # nur Cloud-Modelle (genug Cloud im Katalog → Pool ist cloud-only)
    payload = json.dumps({
        "orchestrator": "glm-5.2:cloud", "judge": "deepseek-coder:cloud",
        "subagents": ["gpt-oss:120b-cloud", "deepseek-coder:cloud"], "reason": "efficient",
    })
    choice = _run(_FakeClient(payload))
    assert choice["orchestrator"] == "glm-5.2:cloud"
    assert choice["subagents"] == ["gpt-oss:120b-cloud", "deepseek-coder:cloud"]
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
