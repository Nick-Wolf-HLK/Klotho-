"""Automatische Rollen-Zuweisung: ein Modell wählt aus dem verfügbaren Ollama-
Katalog die passenden Modelle für Orchestrator, Judge und Subagenten — nach
Kriterien für einen TOKEN-EFFIZIENTEN Code-Audit.

Primär entscheidet ein LLM (der „Auswähler"); schlägt das fehl oder liefert es
Unbrauchbares, greift eine deterministische Heuristik. Beide meiden bewusst
schwere Reasoning-/„pro"-Modelle (z. B. deepseek-*-pro) für die Subagenten, weil
die pro Call massig internes Nachdenken erzeugen.
"""
from __future__ import annotations

import json

from .agent import _extract_json
from .llm_client import LLMClient

# Marker im Modellnamen → schweres Reasoning (für Bulk-Audit meiden).
REASONING_MARKERS = (
    "-pro", ":pro", "pro-", "reason", "reasoning", "thinking", "think",
    "-r1", "r1-", ":r1", "qwq", "o1", "o3", "marco",
)
# Marker → code-stark und/oder schlank (für Subagenten/Audit bevorzugen).
CODE_MARKERS = ("coder", "code", "codestral", "starcoder", "gpt-oss",
                "qwen", "gemma", "mistral", "llama", "flash", "devstral")


def _base(model: str) -> str:
    return model.lower()


def is_cloud(model: str) -> bool:
    """Ollama-Cloud-Modell (Suffix :cloud oder -cloud)."""
    return "cloud" in _base(model)


def _candidate_pool(available: list[str], prefer_cloud: bool) -> list[str]:
    """Bei genügend Cloud-Modellen nur Cloud verwenden (lokale Modelle parallel
    würden den lokalen Ollama-Daemon überlasten). Sonst alles."""
    if not prefer_cloud:
        return available
    cloud = [m for m in available if is_cloud(m)]
    return cloud if len(cloud) >= 3 else available


def is_reasoning(model: str) -> bool:
    m = _base(model)
    # deepseek-coder ist KEIN Reasoning-Modell, deepseek-*-pro/r1 schon
    if "coder" in m:
        return False
    return any(mk in m for mk in REASONING_MARKERS)


def _efficiency_score(model: str) -> int:
    m = _base(model)
    score = 0
    if any(mk in m for mk in CODE_MARKERS):
        score += 2
    if is_reasoning(model):
        score -= 5
    if "flash" in m or "20b" in m or "mini" in m or "small" in m:
        score += 1   # ausdrücklich schlank
    return score


def heuristic_select(available: list[str], *, prefer_cloud: bool = True) -> dict:
    """Deterministische Auswahl ohne LLM (Fallback & Auswähler-Bestimmung)."""
    if not available:
        return {"orchestrator": "", "judge": "", "subagents": [], "reason": "keine Modelle"}
    pool = _candidate_pool(available, prefer_cloud)
    ranked = sorted(pool, key=lambda m: (-_efficiency_score(m), m))
    efficient = [m for m in ranked if not is_reasoning(m)] or ranked

    subagents = efficient[:4]
    # Judge: schlankes, neutrales Modell (gpt-oss:20b bevorzugt)
    judge = next((m for m in efficient if _base(m) == "gpt-oss:20b"),
                 next((m for m in efficient if "gpt-oss" in _base(m)), efficient[-1]))
    # Orchestrator: fähig, darf größer sein; nimm das stärkste effiziente, sonst irgendeins
    orchestrator = next((m for m in efficient if any(x in _base(m) for x in ("glm", "120b", "large", "qwen"))),
                        efficient[0])
    return {
        "orchestrator": orchestrator,
        "judge": judge,
        "subagents": subagents,
        "reason": "Heuristik: code-starke, schlanke Modelle; Reasoning-Modelle gemieden.",
    }


SELECTOR_SYSTEM = (
    "You assign Ollama models to roles for a TOKEN-EFFICIENT code-audit pipeline. "
    "Given the list of AVAILABLE models, choose:\n"
    "- subagents: 3-5 models that are STRONG AT CODE but TOKEN-EFFICIENT. AVOID heavy "
    "reasoning/'pro'/'thinking' models (e.g. deepseek-*-pro, *-r1, qwq, o1) for bulk auditing — "
    "they emit huge internal reasoning per call and burn tokens.\n"
    "- judge: ONE neutral, cheap, reliable model (small is fine; not a heavy reasoning model).\n"
    "- orchestrator: ONE capable model to synthesize the final report.\n"
    "Prefer efficient code models (gpt-oss, gemma, qwen/qwen-coder, codestral, gemini-flash, "
    "mistral). STRONGLY prefer cloud models (id contains 'cloud') — they parallelise well; only "
    "use local models if there aren't enough cloud ones. Every id MUST be copied EXACTLY from the "
    "available list.\n"
    'Reply with ONE JSON object, nothing else: {"orchestrator":"<id>","judge":"<id>",'
    '"subagents":["<id>",...],"reason":"one short sentence why"}'
)


def _validate(choice: dict, available: list[str]) -> dict | None:
    avail = set(available)
    orch = choice.get("orchestrator")
    judge = choice.get("judge")
    subs = [s for s in (choice.get("subagents") or []) if s in avail]
    if orch not in avail or judge not in avail or not subs:
        return None
    return {
        "orchestrator": orch,
        "judge": judge,
        "subagents": subs[:6],
        "reason": str(choice.get("reason", "")).strip(),
    }


async def select_models(
    client: LLMClient,
    selector_model: str,
    available: list[str],
    task: str = "code audit",
    *,
    prefer_cloud: bool = True,
) -> dict:
    """Lässt ``selector_model`` die Rollen zuweisen; fällt bei Fehler/Unbrauchbarem
    auf die Heuristik zurück. Liefert {orchestrator, judge, subagents, reason}.
    Bei genügend Cloud-Modellen werden nur Cloud-Modelle angeboten."""
    pool = _candidate_pool(available, prefer_cloud) if available else available
    if not pool:
        return heuristic_select(available, prefer_cloud=prefer_cloud)
    listing = "\n".join(f"- {m}" for m in pool)
    user = f"Task: {task} (favor token efficiency).\n\nAVAILABLE MODELS:\n{listing}"
    try:
        result = await client.chat(
            selector_model,
            [{"role": "system", "content": SELECTOR_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.1,
        )
        blob = _extract_json(result.text or "")
        choice = json.loads(blob) if blob else None
        validated = _validate(choice, pool) if isinstance(choice, dict) else None
        if validated:
            return validated
    except Exception:
        pass
    return heuristic_select(available, prefer_cloud=prefer_cloud)
