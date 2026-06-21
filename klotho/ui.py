"""Rich-based UI helpers."""
from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import i18n
from .plan_schema import JudgeReport, MasterPlan, SubagentResponse

console = Console()


def show_subagent_responses(responses: list[SubagentResponse]) -> None:
    table = Table(title="Subagent Responses", show_lines=True)
    table.add_column("Agent", style="cyan")
    table.add_column("Model", style="magenta")
    table.add_column("Time", justify="right")
    table.add_column("Status")
    table.add_column("Preview", overflow="fold")
    for r in responses:
        status = "[green]ok[/]" if not r.error else f"[red]err: {r.error[:40]}[/]"
        preview = (r.response[:120] + "…") if len(r.response) > 120 else r.response
        table.add_row(r.agent, r.model, f"{r.elapsed_ms}ms", status, preview)
    console.print(table)


def show_judge_report(report: JudgeReport) -> None:
    table = Table(title="Judge Report", show_lines=True)
    table.add_column("Agent", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Weight", justify="right", style="yellow")
    table.add_column("Criteria")
    for v in report.verdicts:
        crit = "\n".join(f"{c.criterion}: {c.score:.1f}" for c in v.criteria)
        table.add_row(v.agent, f"{v.total_score:.2f}", f"{v.weight:.0%}", crit)
    console.print(table)
    if report.summary:
        console.print(Panel(Markdown(report.summary), title="Judge Summary"))


def show_master_plan(plan: MasterPlan) -> None:
    body = f"## {plan.title}\n\n{plan.summary}\n\n"
    for s in plan.steps:
        body += f"### {s.id}. {s.title} ({s.action.value})\n{s.description}\n\n"
    if plan.rationale:
        body += f"**Rationale:** {plan.rationale}\n"
    if plan.sources:
        src = ", ".join(f"{k}: {v:.0%}" for k, v in plan.sources.items())
        body += f"\n**Sources:** {src}\n"
    console.print(Panel(Markdown(body), title="Master Plan", border_style="green"))


def klotho_say(msg: str) -> None:
    """Klotho spricht zum Nutzer — führend und erklärend."""
    console.print(f"[bold cyan]◈ Klotho[/] [dim]·[/] {msg}")


def show_model_ranking(report) -> None:
    """Kompaktes Modell-Ranking dieses Laufs (Judge-Scores, absteigend)."""
    verdicts = getattr(report, "verdicts", None)
    if not verdicts:
        return
    ranked = sorted(verdicts, key=lambda v: v.total_score, reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    parts = []
    for i, v in enumerate(ranked):
        marker = medals[i] if i < 3 else f"{i + 1}."
        parts.append(f"{marker} [cyan]{v.agent}[/] [dim]({v.total_score:.1f})[/]")
    label = i18n.t("🏆 Modell-Ranking:", "🏆 Model ranking:")
    console.print(f"[bold]{label}[/]  " + "  ·  ".join(parts))


def show_bug_report(md: str) -> str | None:
    """Zeigt den konsolidierten Bug-Report und speichert ihn als Markdown-Datei
    (zum Weitergeben an ein Fix-LLM oder zum Selber-Fixen)."""
    console.print(Panel(Markdown(md), title=i18n.t("[bold red]Bug-Report[/]", "[bold red]Bug Report[/]"),
                        border_style="red"))
    from datetime import datetime
    from pathlib import Path

    path = Path.cwd() / f"klotho-bugreport-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    try:
        path.write_text(md, encoding="utf-8")
        console.print(i18n.t(f"[green]✔ Bug-Report gespeichert:[/] {path}",
                             f"[green]✔ Bug report saved:[/] {path}"))
        return str(path)
    except OSError as exc:
        console.print(i18n.t(f"[yellow]Konnte Report nicht speichern: {exc}[/]",
                             f"[yellow]Could not save report: {exc}[/]"))
        return None


def info(msg: str) -> None:
    console.print(f"[dim]{msg}[/]")


def success(msg: str) -> None:
    console.print(f"[green]✔ {msg}[/]")


def error(msg: str) -> None:
    console.print(f"[red]✘ {msg}[/]")