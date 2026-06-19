"""Interactive REPL session — Claude Code / Codex style with dropdown menus.

Start with:  orchestrator
Then you get a banner, pick orchestrator / judge / subagents via arrow-key
dropdowns, type your topic, and the pipeline runs.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import questionary
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import intro, ui
from .config import (
    OrchestratorConfig,
    SubagentConfig,
    all_known_models,
    load_opencode_base_url,
)
from .executor import Executor
from .judge import judge_responses
from .llm_client import LLMClient
from .subagent import run_subagents_parallel
from .synthesizer import synthesize_plan

console = Console()


def _pick_orchestrator(available: list[str]) -> str:
    """Dropdown single-select for the orchestrator model."""
    default = "glm-5.2:cloud" if "glm-5.2:cloud" in available else available[0]
    return questionary.select(
        "Welches Modell soll ORCHESTRATOR sein (plant & synthetisiert)?",
        choices=available,
        default=default,
        use_arrow_keys=True,
    ).ask()


def _pick_judge(available: list[str]) -> str:
    """Dropdown single-select for the judge model."""
    default = "gpt-oss:20b" if "gpt-oss:20b" in available else available[0]
    return questionary.select(
        "Welches Modell soll JUDGE sein (bewertet neutral)?",
        choices=available,
        default=default,
        use_arrow_keys=True,
    ).ask()


def _pick_subagents(available: list[str]) -> list[str]:
    """Multi-select dropdown for subagents (space to toggle, enter to confirm)."""
    return questionary.checkbox(
        "Wähle deine SUBAGENTEN (Leertaste = an/abwählen, Enter = bestätigen):",
        choices=available,
    ).ask()


def _ask_topic() -> Optional[str]:
    """Fragt nach dem Thema.

    Wiederholt bei leerer Eingabe (Klotho fragt freundlich nach) und bricht
    nur bei Esc/Ctrl+C ab (questionary liefert dann None).
    """
    while True:
        topic = questionary.text("Worüber soll ich planen?").ask()
        if topic is None:  # Esc / Ctrl+C
            return None
        topic = topic.strip()
        if topic:
            return topic
        ui.klotho_say(
            "Du hast noch kein Thema genannt — sag mir kurz, worüber ich "
            "planen soll. [dim](Esc bricht ab.)[/]"
        )


def _ask_mode() -> tuple[bool, bool]:
    """Ask whether to execute and whether dry-run."""
    mode = questionary.select(
        "Was soll am Ende passieren?",
        choices=[
            "Nur Plan anzeigen (nicht ausführen)",
            "Plan ausführen (vollautomatisch)",
            "Plan ausführen — nur simulieren (dry-run)",
        ],
        default="Nur Plan anzeigen (nicht ausführen)",
        use_arrow_keys=True,
    ).ask()
    if mode.startswith("Plan ausführen (vollautomatisch"):
        return True, False
    if mode.startswith("Plan ausführen — nur simulieren"):
        return True, True
    return False, False


def _ask_refine() -> bool:
    """Ask whether to refine the prompt first."""
    return questionary.confirm(
        "Soll der Orchestrator deinen Prompt vorher verfeinern?",
        default=True,
    ).ask()


def _confirm_run() -> bool:
    return questionary.confirm("Losgehen?", default=True).ask()


def _run_pipeline(
    cfg: OrchestratorConfig,
    prompt: str,
    *,
    plan_only: bool,
    dry_run: bool,
    refine: bool,
) -> None:
    client = LLMClient(base_url=cfg.base_url)
    subagents = cfg.subagents
    if not subagents:
        ui.error("Keine Subagenten gewählt.")
        return

    refine_prompt: Optional[str] = None
    if refine:
        ui.info(f"Orchestrator ({cfg.orchestrator_model}) verfeinert den Prompt…")
        refine_task = asyncio.run(
            client.chat(
                cfg.orchestrator_model,
                [
                    {
                        "role": "system",
                        "content": (
                            "Refine the user's planning prompt so it elicits the "
                            "best possible actionable plan from a planning "
                            "subagent. Output ONLY the refined prompt, in the "
                            "same language as the user's prompt."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
        )
        refine_prompt = refine_task.text
        console.print(Panel(Markdown(refine_prompt), title="Verfeinerter Prompt", border_style="cyan"))

    ui.info(f"Schicke an {len(subagents)} Subagenten parallel…")
    responses = asyncio.run(
        run_subagents_parallel(client, subagents, prompt, refine_prompt=refine_prompt)
    )
    ui.show_subagent_responses(responses)

    ui.info(f"Bewerte mit {cfg.judge_model}…")
    report = asyncio.run(
        judge_responses(client, cfg.judge_model, prompt, responses, cfg.rubric)
    )
    ui.show_judge_report(report)

    synth_model = cfg.orchestrator_model
    ui.info(f"Synthetisiere Masterplan mit {synth_model}…")
    plan = asyncio.run(
        synthesize_plan(client, synth_model, prompt, responses, report)
    )
    ui.show_master_plan(plan)

    if plan_only:
        ui.success("Plan-only: fertig.")
        return

    dry = dry_run or cfg.execution.dry_run_default
    ui.info(f"Führe Plan aus (dry_run={dry})…")
    executor = Executor(
        root_lock=cfg.execution.root_lock,
        log_file=cfg.execution.log_file,
        dry_run=dry,
    )
    results = executor.execute(plan)
    for r in results:
        status = "ok" if r.get("ok") else "FAILED"
        ui.info(f"  Schritt {r['step_id']}: {r['title']} -> {status}")
    ui.success("Ausführung beendet.")


def _show_summary(orch: str, judge: str, subs: list[str], execute: bool, dry: bool, refine: bool) -> None:
    console.print()
    table = Table(title="Konfiguration", show_header=True, border_style="dim")
    table.add_column("Rolle", style="cyan")
    table.add_column("Modell", style="magenta")
    table.add_row("Orchestrator", orch)
    table.add_row("Judge", judge)
    table.add_row("Subagenten", ", ".join(subs))
    table.add_row("Modus", "Execute" if execute else "Plan-only")
    table.add_row("Dry-Run", "ja" if dry else "nein")
    table.add_row("Refine", "ja" if refine else "nein")
    console.print(table)
    console.print()


def start_interactive() -> None:
    """Main interactive session loop with dropdown menus."""
    intro.play_intro(console)

    base_url = load_opencode_base_url() or "http://127.0.0.1:11434/v1"
    available = all_known_models()
    if not available:
        console.print("[red]Keine Modelle gefunden. Läuft Ollama?[/]")
        return

    intro.show_onboarding(console)

    first = True
    while True:
        if not first:
            intro.compact_header(console)
        first = False
        console.print()
        console.print(Panel.fit("[bold]Neue Session[/]", border_style="blue"))

        # 1. Orchestrator-Modell wählen (Dropdown)
        orch_model = _pick_orchestrator(available)
        if not orch_model:
            console.print("[dim]Abgebrochen.[/]")
            return

        # 2. Judge-Modell wählen (Dropdown)
        judge_model = _pick_judge(available)
        if not judge_model:
            console.print("[dim]Abgebrochen.[/]")
            return

        # 3. Subagenten wählen (Multi-Select Dropdown)
        #    Eigene Retry-Schleife: leere Auswahl darf NICHT die ganze Session
        #    verwerfen (Orchestrator/Judge bleiben erhalten).
        while True:
            picked = _pick_subagents(available)
            if picked is None:  # Esc / Ctrl+C → Session abbrechen
                console.print("[dim]Abgebrochen.[/]")
                return
            if picked:
                break
            console.print(
                "[yellow]Mindestens einen Subagenten mit der "
                "[bold]Leertaste[/] markieren, dann Enter.[/]"
            )

        # 4. Thema/Prompt abfragen (eigene Nachfrage-Schleife in _ask_topic)
        topic = _ask_topic()
        if topic is None:  # Esc / Ctrl+C
            console.print("[dim]Abgebrochen.[/]")
            return

        # 5. Modus abfragen (Dropdown)
        execute, dry = _ask_mode()

        # 6. Refine?
        refine = _ask_refine()

        # Zusammenfassung anzeigen
        _show_summary(
            orch_model, judge_model, picked, execute, dry, refine
        )

        # Bestätigen
        if not _confirm_run():
            console.print("[dim]Abgebrochen — zurück zum Start.[/]")
            continue

        cfg = OrchestratorConfig(
            orchestrator_model=orch_model,
            judge_model=judge_model,
            subagents=[
                SubagentConfig(name=m.split(":")[0], model=m, order=i + 1)
                for i, m in enumerate(picked)
            ],
            base_url=base_url,
        )

        try:
            _run_pipeline(
                cfg,
                topic,
                plan_only=not execute,
                dry_run=dry,
                refine=refine,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Abgebrochen.[/]")
        except Exception as exc:
            ui.error(f"Fehler: {exc}")

        # Loop: weitere Session?
        console.print()
        again = questionary.confirm("Noch eine Session?", default=False).ask()
        if not again:
            console.print("[dim]Tschüss! 👋[/]")
            break