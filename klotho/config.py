"""Configuration loading and interactive editing."""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import questionary
except ImportError:  # optional until first /config run
    questionary = None  # type: ignore


DEFAULT_CONFIG_PATH = Path("models.toml")
OPENCODE_CONFIG_PATH = Path.home() / ".opencode.json"
OPENCODE_ENV_VAR = "OPENCODE_CONFIG_CONTENT"
OLLAMA_CONFIG_PATH = Path.home() / ".ollama" / "config.json"


@dataclass
class SubagentConfig:
    name: str
    model: str
    order: int = 0


@dataclass
class ExecutionConfig:
    root_lock: str = "."
    log_file: str = "~/.klotho/log.jsonl"
    dry_run_default: bool = False


@dataclass
class RubricConfig:
    criteria: list[str] = field(
        default_factory=lambda: ["completeness", "feasibility", "originality", "depth"]
    )


@dataclass
class OrchestratorConfig:
    orchestrator_model: str = "glm-5.2:cloud"
    judge_model: str = "gpt-oss:20b"
    subagents: list[SubagentConfig] = field(default_factory=list)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    rubric: RubricConfig = field(default_factory=RubricConfig)
    base_url: str = "http://127.0.0.1:11434/v1"
    agent_max_iterations: int = 60  # Schritte pro agentischem Subagenten


def _load_opencode_raw() -> dict:
    """Read opencode config from env var first, then from ~/.opencode.json."""
    env = os.environ.get(OPENCODE_ENV_VAR)
    if env:
        try:
            return json.loads(env)
        except json.JSONDecodeError:
            pass
    if OPENCODE_CONFIG_PATH.exists():
        try:
            return json.loads(OPENCODE_CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def load_opencode_models() -> list[str]:
    """Read model ids registered in opencode config (cloud models)."""
    data = _load_opencode_raw()
    models: list[str] = []
    for provider in data.get("provider", {}).values():
        for mid in (provider.get("models") or {}).keys():
            models.append(mid)
    return sorted(set(models))


def load_ollama_integration_models() -> list[str]:
    """Read model ids registered in ~/.ollama/config.json integrations.

    Ollama stores per-integration model ids under ``integrations.<name>.models``
    (a plain list). These include ``:cloud`` models that the local /api/tags
    endpoint does NOT return, so we surface them in the picker alongside the
    locally pulled models.
    """
    if not OLLAMA_CONFIG_PATH.exists():
        return []
    try:
        data = json.loads(OLLAMA_CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    models: list[str] = []
    for integration in (data.get("integrations") or {}).values():
        for mid in integration.get("models") or []:
            models.append(mid)
    return sorted(set(models))


def load_opencode_base_url() -> Optional[str]:
    """Extract the ollama baseURL from opencode config (if present)."""
    data = _load_opencode_raw()
    for provider in data.get("provider", {}).values():
        opts = provider.get("options") or {}
        if "baseURL" in opts:
            return opts["baseURL"]
    return None


def list_local_ollama_models(base_url: str = "http://127.0.0.1:11434") -> list[str]:
    """Return models known to the local ollama daemon (best-effort)."""
    import httpx

    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=3.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def all_known_models(base_url: str = "http://127.0.0.1:11434") -> list[str]:
    """Union of: registered cloud models (opencode config + ollama integrations),
    locally pulled ollama models, and the cached Ollama-Cloud catalog."""
    from . import cloud_registry  # lazy: vermeidet harte httpx-Abhängigkeit beim Import
    combined = set(load_opencode_models())
    combined.update(load_ollama_integration_models())
    combined.update(list_local_ollama_models(base_url))
    combined.update(cloud_registry.cached_cloud_models())
    return sorted(combined)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> OrchestratorConfig:
    if not path.exists():
        return _default_config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    orch = data.get("orchestrator", {})
    judge = data.get("judge", {})
    subs_raw = data.get("subagents", [])
    exec_raw = data.get("execution", {})
    rubric_raw = data.get("rubric", {})
    agent_raw = data.get("agent", {})
    subagents = [
        SubagentConfig(
            name=s.get("name", s.get("model", "?")),
            model=s["model"],
            order=int(s.get("order", i + 1)),
        )
        for i, s in enumerate(subs_raw)
    ]
    return OrchestratorConfig(
        orchestrator_model=orch.get("model", "glm-5.2:cloud"),
        judge_model=judge.get("model", "gpt-oss:20b"),
        subagents=subagents,
        execution=ExecutionConfig(
            root_lock=exec_raw.get("root_lock", "."),
            log_file=exec_raw.get("log_file", "~/.klotho/log.jsonl"),
            dry_run_default=bool(exec_raw.get("dry_run_default", False)),
        ),
        rubric=RubricConfig(criteria=rubric_raw.get("criteria", [
            "completeness", "feasibility", "originality", "depth"
        ])),
        base_url=orch.get("base_url") or load_opencode_base_url() or "http://127.0.0.1:11434/v1",
        agent_max_iterations=int(agent_raw.get("max_iterations", 60)),
    )


def _default_config() -> OrchestratorConfig:
    return OrchestratorConfig(
        orchestrator_model="glm-5.2:cloud",
        judge_model="gpt-oss:20b",
        subagents=[
            SubagentConfig("minimax", "minimax-m2.7:cloud", 1),
            SubagentConfig("nemotron", "nemotron-3-super:cloud", 2),
            SubagentConfig("qwen", "qwen3.5:27b", 3),
        ],
        base_url=load_opencode_base_url() or "http://127.0.0.1:11434/v1",
    )


def save_config(cfg: OrchestratorConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    lines: list[str] = []
    lines.append("[orchestrator]")
    lines.append(f'model = "{cfg.orchestrator_model}"')
    lines.append(f'base_url = "{cfg.base_url}"')
    lines.append("")
    lines.append("[judge]")
    lines.append(f'model = "{cfg.judge_model}"')
    lines.append("")
    for s in sorted(cfg.subagents, key=lambda x: x.order):
        lines.append("[[subagents]]")
        lines.append(f'name = "{s.name}"')
        lines.append(f'model = "{s.model}"')
        lines.append(f"order = {s.order}")
        lines.append("")
    lines.append("[execution]")
    lines.append(f'root_lock = "{cfg.execution.root_lock}"')
    lines.append(f'log_file = "{cfg.execution.log_file}"')
    lines.append(f'dry_run_default = {str(cfg.execution.dry_run_default).lower()}')
    lines.append("")
    lines.append("[rubric]")
    lines.append(f'criteria = {[c for c in cfg.rubric.criteria]}')
    lines.append("")
    lines.append("[agent]")
    lines.append(f'max_iterations = {cfg.agent_max_iterations}  # Schritte pro agentischem Subagenten')
    lines.append("")
    path.write_text("\n".join(lines))


def interactive_config(
    path: Path = DEFAULT_CONFIG_PATH,
    base_url: str = "http://127.0.0.1:11434",
) -> OrchestratorConfig:
    """Run the /config TUI flow using questionary."""
    if questionary is None:
        raise RuntimeError(
            "questionary not installed. Run: pip install questionary"
        )
    models = all_known_models(base_url)
    if not models:
        print("No models found via opencode config or local ollama.")
        return _default_config()

    orch = questionary.select(
        "Which model should be the ORCHESTRATOR (planner)?",
        choices=models,
    ).ask()
    judge = questionary.select(
        "Which model should be the JUDGE (neutral evaluator)?",
        choices=models,
    ).ask()
    sub_choices = questionary.checkbox(
        "Select SUBAGENT models (these draft plans in parallel):",
        choices=models,
    ).ask()
    subagents: list[SubagentConfig] = []
    for i, m in enumerate(sub_choices, start=1):
        name = questionary.text(
            f"Display name for {m}:", default=m.split(":")[0]
        ).ask()
        subagents.append(SubagentConfig(name=name or m, model=m, order=i))

    cfg = OrchestratorConfig(
        orchestrator_model=orch,
        judge_model=judge,
        subagents=subagents or _default_config().subagents,
        base_url=load_opencode_base_url() or "http://127.0.0.1:11434/v1",
    )
    save_config(cfg, path)
    return cfg