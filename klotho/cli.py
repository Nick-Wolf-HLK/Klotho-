"""CLI entry point: python -m klotho ..."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer

from . import codebase, compress, ui
from .config import (
    DEFAULT_CONFIG_PATH,
    OrchestratorConfig,
    interactive_config,
    load_config,
)
from .executor import Executor
from .judge import judge_responses
from .llm_client import LLMClient
from .subagent import run_subagents_parallel
from .synthesizer import synthesize_plan

app = typer.Typer(
    help="Klotho — Multi-LLM Orchestrator: dispatch a prompt to several LLMs, "
    "judge the responses and synthesize a master plan. Viele Fäden, ein Plan.",
    invoke_without_command=False,
)


def _load(path: Path) -> OrchestratorConfig:
    if not path.exists():
        ui.info(f"No config at {path}, using defaults. Run '/config' to customize.")
    return load_config(path)


def _resolve_root(path: Optional[str]) -> Optional[str]:
    """Validiert einen Projektordner; die Subagenten durchsuchen ihn agentisch."""
    if not path:
        return None
    root = Path(path).expanduser()
    if not root.is_dir():
        ui.error(f"Project path not found: {root}")
        raise typer.Exit(1)
    n = len(codebase.collect_source_files(root))
    ui.info(f"Project: {root} — {n} source files. Subagents will explore it (read-only).")
    return str(root)


def _run_pipeline(
    cfg: OrchestratorConfig,
    prompt: str,
    *,
    plan_only: bool,
    dry_run: bool,
    refine: bool,
    root: Optional[str] = None,
) -> None:
    client = LLMClient(base_url=cfg.base_url)
    subagents = cfg.subagents
    if not subagents:
        ui.error("No subagents configured. Run '/config' first.")
        raise typer.Exit(1)

    refine_prompt: Optional[str] = None
    if refine:
        ui.info(f"Asking orchestrator ({cfg.orchestrator_model}) to refine prompt…")
        refine_task = asyncio.run(
            client.chat(
                cfg.orchestrator_model,
                [
                    {
                        "role": "system",
                        "content": (
                            "Refine the user's planning prompt so it elicits the "
                            "best possible actionable plan from a planning "
                            "subagent. Output ONLY the refined prompt."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
        )
        refine_prompt = refine_task.text
        ui.info(f"Refined prompt:\n{refine_prompt}\n")

    if root:
        ui.info("Subagents explore the project folder agentically (read-only)…")
    ui.info(f"Dispatching to {len(subagents)} subagents in parallel…")
    responses = asyncio.run(
        run_subagents_parallel(
            client, subagents, prompt, refine_prompt=refine_prompt, root=root
        )
    )
    ui.show_subagent_responses(responses)

    comp_stats = compress.CompressionStats(level=cfg.compression)

    ui.info(f"Judging with {cfg.judge_model}…")
    report = asyncio.run(
        judge_responses(
            client, cfg.judge_model, prompt, responses, cfg.rubric,
            compression=cfg.compression, stats=comp_stats,
        )
    )
    ui.show_judge_report(report)

    synth_model = cfg.orchestrator_model
    ui.info(f"Synthesizing master plan with {synth_model}…")
    plan = asyncio.run(
        synthesize_plan(
            client, synth_model, prompt, responses, report,
            compression=cfg.compression, stats=comp_stats,
        )
    )
    ui.show_master_plan(plan)
    ui.show_compression_stats(comp_stats)

    if plan_only:
        ui.success("Plan-only mode: stopping here.")
        return

    dry = dry_run or cfg.execution.dry_run_default
    ui.info(f"Executing plan (dry_run={dry})…")
    executor = Executor(
        root_lock=cfg.execution.root_lock,
        log_file=cfg.execution.log_file,
        dry_run=dry,
    )
    results = executor.execute(plan)
    for r in results:
        status = "ok" if r.get("ok") else "FAILED"
        ui.info(f"  step {r['step_id']}: {r['title']} -> {status}")
    ui.success("Execution complete.")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    prompt: Optional[str] = typer.Argument(
        None, help="Planning prompt (skip to launch interactive mode)."
    ),
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", "-c", help="Path to models.toml"
    ),
    plan_only: bool = typer.Option(
        False, "--plan-only", help="Only produce a plan, do not execute (default)."
    ),
    execute: bool = typer.Option(
        False, "--execute", help="Execute the produced plan."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would happen without running it."
    ),
    refine: bool = typer.Option(
        False, "--refine", help="Let orchestrator LLM refine the prompt first."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", help="Project folder the subagents explore agentically (read-only)."
    ),
) -> None:
    """Klotho: dispatch a prompt, judge responses, synthesize a plan."""
    if ctx.invoked_subcommand is not None:
        return
    # No prompt given → launch interactive REPL mode
    if not prompt:
        from .interactive import start_interactive
        start_interactive()
        return
    # Typer mit positionalem callback-Argument schluckt Subcommand-Namen als
    # 'prompt'. Wenn der 'prompt' exakt ein bekannter Subcommand ist (und keine
    # weiteren Pipeline-Flags gesetzt sind), an diesen delegieren.
    if prompt == "models":
        models_cmd()
        return
    if prompt == "config":
        config_cmd(config=config)
        return
    cfg = _load(config)
    plan_only_mode = plan_only or (not execute and not dry_run)
    _run_pipeline(
        cfg, prompt, plan_only=plan_only_mode, dry_run=dry_run, refine=refine,
        root=_resolve_root(context),
    )


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Planning prompt to dispatch."),
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", "-c", help="Path to models.toml"
    ),
    plan_only: bool = typer.Option(
        False, "--plan-only", help="Only produce a plan, do not execute."
    ),
    execute: bool = typer.Option(
        False, "--execute", help="Execute the produced plan (default if neither flag set)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would happen without running it."
    ),
    refine: bool = typer.Option(
        False, "--refine", help="Let orchestrator LLM refine the prompt first."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", help="Project folder the subagents explore agentically (read-only)."
    ),
) -> None:
    """Dispatch PROMPT to subagents, judge, synthesize and (optionally) execute."""
    cfg = _load(config)
    plan_only_mode = plan_only or (not execute and not dry_run)
    _run_pipeline(
        cfg, prompt, plan_only=plan_only_mode, dry_run=dry_run, refine=refine,
        root=_resolve_root(context),
    )


@app.command("config")
def config_cmd(
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", "-c", help="Path to models.toml"
    ),
) -> None:
    """Interactively configure models and roles."""
    cfg = interactive_config(config)
    ui.success(f"Saved config to {config}")
    ui.info(f"Orchestrator: {cfg.orchestrator_model}")
    ui.info(f"Judge: {cfg.judge_model}")
    for s in cfg.subagents:
        ui.info(f"  subagent: {s.name} -> {s.model} (order {s.order})")


@app.command("models")
def models_cmd() -> None:
    """List all models known via opencode config + local ollama."""
    from .config import all_known_models

    for m in all_known_models():
        print(m)


if __name__ == "__main__":
    app()