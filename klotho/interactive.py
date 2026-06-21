"""Interactive REPL session — Claude Code / Codex style with dropdown menus.

Start with:  orchestrator
Then you get a banner, pick orchestrator / judge / subagents via arrow-key
dropdowns, type your topic, and the pipeline runs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import questionary
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import audit, codebase, i18n, intro, live, ui
from .config import (
    OrchestratorConfig,
    SubagentConfig,
    all_known_models,
    load_config,
    load_opencode_base_url,
)
from .executor import Executor
from .judge import equal_weight_report, judge_responses
from .llm_client import LLMClient
from .subagent import run_subagents_parallel
from .synthesizer import synthesize_plan

console = Console()


def _auto_select_models(available: list[str], base_url: str):
    """Bietet an, die Modelle automatisch wählen zu lassen (ein Modell weist die
    Rollen token-effizient zu). Liefert {orchestrator, judge, subagents} oder None
    (→ manuelle Auswahl)."""
    from . import model_select

    if not questionary.confirm(
        i18n.t("Modelle automatisch wählen lassen? (empfohlen, token-effizient)",
               "Let Klotho pick the models automatically? (recommended, token-efficient)"),
        default=True,
    ).ask():
        return None

    selector = model_select.heuristic_select(available)["orchestrator"]
    client = LLMClient(base_url=base_url)
    with console.status(
        i18n.t(f"[cyan]{selector} wählt passende Modelle …[/]",
               f"[cyan]{selector} is picking models …[/]"), spinner="dots"):
        try:
            choice = asyncio.run(model_select.select_models(
                client, selector, available, task="code audit / bug report"))
        except Exception:
            choice = model_select.heuristic_select(available)

    body = (
        f"[bold]Orchestrator:[/] {choice['orchestrator']}\n"
        f"[bold]Judge:[/] {choice['judge']}\n"
        f"[bold]{i18n.t('Subagenten', 'Subagents')}:[/] {', '.join(choice['subagents'])}\n"
    )
    if choice.get("reason"):
        body += f"\n[dim]{choice['reason']}[/]"
    console.print(Panel(body, title=i18n.t("[bold cyan]🤖 Automatische Modellauswahl[/]",
                                           "[bold cyan]🤖 Automatic model selection[/]"),
                        border_style="cyan"))
    if questionary.confirm(i18n.t("Übernehmen?", "Use these?"), default=True).ask():
        return choice
    return None


def _pick_orchestrator(available: list[str]) -> str:
    default = "glm-5.2:cloud" if "glm-5.2:cloud" in available else available[0]
    return questionary.select(
        i18n.t("Welches Modell soll ORCHESTRATOR sein (plant & synthetisiert)?",
               "Which model should be the ORCHESTRATOR (plans & synthesizes)?"),
        choices=available, default=default, use_arrow_keys=True,
    ).ask()


def _pick_judge(available: list[str]) -> str:
    default = "gpt-oss:20b" if "gpt-oss:20b" in available else available[0]
    return questionary.select(
        i18n.t("Welches Modell soll JUDGE sein (bewertet neutral)?",
               "Which model should be the JUDGE (rates neutrally)?"),
        choices=available, default=default, use_arrow_keys=True,
    ).ask()


def _pick_subagents(available: list[str]) -> list[str]:
    return questionary.checkbox(
        i18n.t("Wähle deine SUBAGENTEN (Leertaste = an/abwählen, Enter = bestätigen):",
               "Pick your SUBAGENTS (space = toggle, Enter = confirm):"),
        choices=available,
    ).ask()


def _ask_topic() -> Optional[str]:
    """Fragt nach dem Thema; wiederholt bei leerer Eingabe, bricht nur bei Esc ab."""
    while True:
        topic = questionary.text(
            i18n.t("Worüber soll ich planen?", "What should I work on?")
        ).ask()
        if topic is None:  # Esc / Ctrl+C
            return None
        topic = topic.strip()
        if topic:
            return topic
        ui.klotho_say(i18n.t(
            "Du hast noch kein Thema genannt — sag mir kurz, worüber ich planen soll. [dim](Esc bricht ab.)[/]",
            "You haven't given me a topic yet — tell me what to work on. [dim](Esc cancels.)[/]"))


def _ask_project_root() -> Optional[str]:
    """Bietet den AKTUELLEN Ordner (cwd) zur agentischen Code-Analyse an."""
    cwd = Path.cwd()
    n = len(codebase.collect_source_files(cwd))
    if n == 0:
        ui.klotho_say(i18n.t(
            f"Kein Quellcode im aktuellen Ordner ([bold]{cwd}[/]) — ich plane ohne Code. "
            "[dim](Starte Klotho im Ordner deines Codes, um ihn zu analysieren.)[/]",
            f"No source code in the current folder ([bold]{cwd}[/]) — planning without code. "
            "[dim](Start Klotho inside your code folder to analyse it.)[/]"))
        return None
    use = questionary.confirm(
        i18n.t(f"Code im aktuellen Ordner analysieren? ({cwd} — {n} Quelldateien)",
               f"Analyse the code in the current folder? ({cwd} — {n} source files)"),
        default=True,
    ).ask()
    if not use:
        return None
    ui.klotho_say(i18n.t(
        "Jeder Subagent durchsucht diesen Ordner selbst (read-only).",
        "Each subagent explores this folder itself (read-only)."))
    return str(cwd)


def _ask_mode() -> tuple[bool, bool]:
    mode = questionary.select(
        i18n.t("Was soll am Ende passieren?", "What should happen at the end?"),
        choices=[
            questionary.Choice(i18n.t("Nur Plan anzeigen (nicht ausführen)",
                                      "Show result only (don't execute)"), "plan"),
            questionary.Choice(i18n.t("Plan ausführen (vollautomatisch)",
                                      "Execute plan (full-auto)"), "exec"),
            questionary.Choice(i18n.t("Plan ausführen — nur simulieren (dry-run)",
                                      "Execute — dry-run only"), "dry"),
        ],
        use_arrow_keys=True,
    ).ask()
    if mode == "exec":
        return True, False
    if mode == "dry":
        return True, True
    return False, False


def _ask_refine() -> bool:
    return questionary.confirm(
        i18n.t("Soll Klotho deinen Prompt vorher verfeinern?",
               "Should Klotho refine your prompt first?"),
        default=True,
    ).ask()


def _confirm_run() -> bool:
    return questionary.confirm(i18n.t("Losgehen?", "Let's go?"), default=True).ask()


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
        ui.error(i18n.t("Keine Subagenten gewählt.", "No subagents selected."))
        return

    refine_prompt: Optional[str] = None
    if refine:
        ui.info(i18n.t(f"Klotho verfeinert deinen Prompt ({cfg.orchestrator_model})…",
                       f"Klotho is refining your prompt ({cfg.orchestrator_model})…"))
        refine_sys = (
            "Refine the user's code-audit request so it elicits CONCRETE, evidence-backed "
            "findings (bugs, logic errors, quality issues) with file:line — NOT a step-by-step "
            "plan of how to look for them. Output ONLY the refined request, in the user's language."
            if root else
            "Refine the user's planning prompt so it elicits the best possible actionable "
            "plan from a planning subagent. Output ONLY the refined prompt, in the user's language."
        )
        refine_task = asyncio.run(
            client.chat(
                cfg.orchestrator_model,
                [
                    {"role": "system", "content": refine_sys},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
        )
        refine_prompt = refine_task.text
        console.print(Panel(Markdown(refine_prompt),
                            title=i18n.t("Verfeinerter Prompt", "Refined prompt"),
                            border_style="cyan"))

    if root:
        # Vorab-Schätzung + Bestätigung, damit niemand blind in einen teuren Lauf rennt.
        from . import coverage as _cov
        all_files = codebase.collect_source_files(root)
        est = _cov.estimate_audit(
            all_files, root, max_files=cfg.coverage_chunk_size,
            max_chars=cfg.coverage_chunk_chars, max_rounds=cfg.coverage_max_rounds)
        ui.klotho_say(i18n.t(
            f"Coverage-Audit: {est['files']} Dateien → {est['calls_total']} LLM-Calls "
            f"({est['chunks']} Chunks × {cfg.coverage_max_rounds} Runde(n), "
            f"max. {cfg.coverage_concurrency} gleichzeitig, 1 Call/Chunk).",
            f"Coverage audit: {est['files']} files → {est['calls_total']} LLM calls "
            f"({est['chunks']} chunks × {cfg.coverage_max_rounds} round(s), "
            f"up to {cfg.coverage_concurrency} concurrent, 1 call/chunk)."))
        if not questionary.confirm(
            i18n.t("Lauf starten?", "Start the run?"), default=True).ask():
            ui.info(i18n.t("Abgebrochen.", "Cancelled."))
            return
        responses = asyncio.run(
            live.run_coverage_with_dashboard(
                console, client, subagents, refine_prompt or prompt,
                root=root,
                chunk_size=cfg.coverage_chunk_size,
                chunk_chars=cfg.coverage_chunk_chars,
                max_concurrency=cfg.coverage_concurrency,
                max_rounds=cfg.coverage_max_rounds,
            )
        )
    else:
        ui.info(i18n.t(f"Schicke an {len(subagents)} Subagenten parallel…",
                       f"Dispatching to {len(subagents)} subagents in parallel…"))
        responses = asyncio.run(
            run_subagents_parallel(
                client, subagents, prompt, refine_prompt=refine_prompt, root=root,
                max_iterations=cfg.agent_max_iterations,
            )
        )
    synth_model = cfg.orchestrator_model

    if root:
        # Coverage-Modus: KEIN Judge-Call. Die Chunks sehen verschiedene Dateien
        # und sind nicht vergleichbar — Gewichtung wäre sinnlos und teuer (der
        # Judge bekäme alle Chunk-Reports auf einmal). Gleichgewichtung genügt;
        # Qualität sichern die Quote-Verifikation + adversariale Stufe.
        n_ok = sum(1 for r in responses if not r.error)
        ui.info(i18n.t(
            f"{n_ok} Chunk-Audits abgeschlossen (Judge im Coverage-Modus übersprungen).",
            f"{n_ok} chunk audits done (judge skipped in coverage mode)."))
        report = equal_weight_report(responses)
    else:
        ui.show_subagent_responses(responses)
        ui.info(i18n.t(f"Bewerte mit {cfg.judge_model}…", f"Judging with {cfg.judge_model}…"))
        try:
            report = asyncio.run(
                judge_responses(client, cfg.judge_model, prompt, responses, cfg.rubric)
            )
            ui.show_judge_report(report)
        except Exception as exc:
            ui.error(i18n.t(
                f"Judge fehlgeschlagen ({str(exc)[:60]}) — nutze Gleichgewichtung.",
                f"Judge failed ({str(exc)[:60]}) — using equal weights."))
            report = equal_weight_report(responses)

    # Code-Modus → konsolidierter Bug-Report (kein Schritt-Plan).
    if root:
        if audit.has_structured_findings(responses):
            # Zwei Stufen: deterministische Quote-Verifikation + adversariale
            # Gegenprüfung jedes Befunds gegen den echten Code.
            with console.status(
                i18n.t("[cyan]Verifiziere & prüfe Befunde adversarial gegen den Quellcode…[/]",
                       "[cyan]Verifying & adversarially reviewing findings against the source…[/]")
            ) as status:
                def _prog(done: int, total: int) -> None:
                    status.update(i18n.t(
                        f"[cyan]Adversariale Gegenprüfung… {done}/{total} Befunde[/]",
                        f"[cyan]Adversarial review… {done}/{total} findings[/]"))
                md = asyncio.run(audit.build_verified_bug_report(
                    client, synth_model, responses, report, root,
                    adjudicate=cfg.coverage_adjudicate, on_progress=_prog))
        else:
            # Fallback: kein Auditor lieferte strukturierte Befunde → LLM-Synthese aus Prosa.
            ui.info(i18n.t(f"Erstelle konsolidierten Bug-Report mit {synth_model}…",
                           f"Building consolidated bug report with {synth_model}…"))
            md = asyncio.run(
                audit.synthesize_bug_report(client, synth_model, prompt, responses, report)
            )
        ui.show_bug_report(md)
        ui.success(i18n.t("Bug-Report fertig.", "Bug report done."))
        return

    ui.info(i18n.t(f"Synthetisiere Masterplan mit {synth_model}…",
                   f"Synthesizing master plan with {synth_model}…"))
    plan = asyncio.run(
        synthesize_plan(client, synth_model, prompt, responses, report)
    )
    ui.show_master_plan(plan)
    ui.show_model_ranking(report)

    if plan_only:
        ui.success(i18n.t("Plan-only: fertig.", "Plan-only: done."))
        return

    dry = dry_run or cfg.execution.dry_run_default
    ui.info(i18n.t(f"Führe Plan aus (dry_run={dry})…", f"Executing plan (dry_run={dry})…"))
    executor = Executor(
        root_lock=cfg.execution.root_lock,
        log_file=cfg.execution.log_file,
        dry_run=dry,
    )
    results = executor.execute(plan)
    for r in results:
        status = "ok" if r.get("ok") else "FAILED"
        ui.info(i18n.t(f"  Schritt {r['step_id']}: {r['title']} -> {status}",
                       f"  Step {r['step_id']}: {r['title']} -> {status}"))
    ui.success(i18n.t("Ausführung beendet.", "Execution complete."))


def _show_summary(orch: str, judge: str, subs: list[str], execute: bool, dry: bool, refine: bool) -> None:
    console.print()
    _yes, _no = i18n.t("ja", "yes"), i18n.t("nein", "no")
    table = Table(title=i18n.t("Konfiguration", "Configuration"), show_header=True, border_style="dim")
    table.add_column(i18n.t("Rolle", "Role"), style="cyan")
    table.add_column(i18n.t("Modell", "Model"), style="magenta")
    table.add_row("Orchestrator", orch)
    table.add_row("Judge", judge)
    table.add_row(i18n.t("Subagenten", "Subagents"), ", ".join(subs))
    table.add_row(i18n.t("Modus", "Mode"), "Execute" if execute else "Plan-only")
    table.add_row("Dry-Run", _yes if dry else _no)
    table.add_row("Refine", _yes if refine else _no)
    console.print(table)
    console.print()


def _ask_language() -> None:
    """Sprachwahl beim Start — steuert UI-Texte UND die Sprache der LLM-Outputs
    (Reports, Bug-Reports, Pläne)."""
    lang = questionary.select(
        "Sprache / Language?",
        choices=[
            questionary.Choice("Deutsch", "de"),
            questionary.Choice("English", "en"),
        ],
        use_arrow_keys=True,
    ).ask()
    i18n.set_language(lang or "de")


def start_interactive() -> None:
    """Main interactive session loop with dropdown menus."""
    _ask_language()
    intro.play_intro(console)

    base_url = load_opencode_base_url() or "http://127.0.0.1:11434/v1"
    base_cfg = load_config()  # agent_max_iterations (models.toml oder Defaults)

    from . import cloud_registry
    if cloud_registry.cache_is_stale():
        with console.status(
            i18n.t("[cyan]Lade verfügbare Ollama-Cloud-Modelle …[/]",
                   "[cyan]Loading available Ollama Cloud models …[/]"), spinner="dots"):
            try:
                cloud_registry.refresh_cache(timeout=15)
            except Exception:
                pass  # offline → nur lokale + konfigurierte Modelle
    available = all_known_models()
    if not available:
        console.print(i18n.t("[red]Keine Modelle gefunden. Läuft Ollama?[/]",
                             "[red]No models found. Is Ollama running?[/]"))
        return

    intro.show_onboarding(console)

    first = True
    while True:
        if not first:
            intro.compact_header(console)
        first = False
        console.print()
        console.print(Panel.fit(i18n.t("[bold]Neue Session[/]", "[bold]New session[/]"),
                                border_style="blue"))

        # 0. Optional: Modelle automatisch wählen lassen (token-effizient)
        auto = _auto_select_models(available, base_url)
        if auto:
            orch_model = auto["orchestrator"]
            judge_model = auto["judge"]
            picked = auto["subagents"]
        else:
            # 1. Orchestrator-Modell wählen (Dropdown)
            orch_model = _pick_orchestrator(available)
            if not orch_model:
                console.print(i18n.t("[dim]Abgebrochen.[/]", "[dim]Cancelled.[/]"))
                return

            # 2. Judge-Modell wählen (Dropdown)
            judge_model = _pick_judge(available)
            if not judge_model:
                console.print(i18n.t("[dim]Abgebrochen.[/]", "[dim]Cancelled.[/]"))
                return

            # 3. Subagenten wählen (Multi-Select Dropdown)
            #    Eigene Retry-Schleife: leere Auswahl darf NICHT die ganze Session
            #    verwerfen (Orchestrator/Judge bleiben erhalten).
            while True:
                picked = _pick_subagents(available)
                if picked is None:  # Esc / Ctrl+C → Session abbrechen
                    console.print(i18n.t("[dim]Abgebrochen.[/]", "[dim]Cancelled.[/]"))
                    return
                if picked:
                    break
                console.print(i18n.t(
                    "[yellow]Mindestens einen Subagenten mit der [bold]Leertaste[/] markieren, dann Enter.[/]",
                    "[yellow]Mark at least one subagent with [bold]space[/], then Enter.[/]"))

        # 4. Thema/Prompt abfragen (eigene Nachfrage-Schleife in _ask_topic)
        topic = _ask_topic()
        if topic is None:  # Esc / Ctrl+C
            console.print(i18n.t("[dim]Abgebrochen.[/]", "[dim]Cancelled.[/]"))
            return

        # 4b. Optional: Projektordner, den die Subagenten agentisch durchsuchen
        root = _ask_project_root()

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
            console.print(i18n.t("[dim]Abgebrochen — zurück zum Start.[/]",
                                 "[dim]Cancelled — back to start.[/]"))
            continue

        cfg = OrchestratorConfig(
            orchestrator_model=orch_model,
            judge_model=judge_model,
            subagents=[
                SubagentConfig(name=m.split(":")[0], model=m, order=i + 1)
                for i, m in enumerate(picked)
            ],
            base_url=base_url,
            agent_max_iterations=base_cfg.agent_max_iterations,
        )

        try:
            _run_pipeline(
                cfg,
                topic,
                plan_only=not execute,
                dry_run=dry,
                refine=refine,
                root=root,
            )
        except KeyboardInterrupt:
            console.print(i18n.t("\n[yellow]Abgebrochen.[/]", "\n[yellow]Cancelled.[/]"))
        except Exception as exc:
            ui.error(i18n.t(f"Fehler: {exc}", f"Error: {exc}"))

        # Loop: weitere Session?
        console.print()
        again = questionary.confirm(
            i18n.t("Noch eine Session?", "Another session?"), default=False).ask()
        if not again:
            console.print(i18n.t("[dim]Tschüss! 👋[/]", "[dim]Bye! 👋[/]"))
            break