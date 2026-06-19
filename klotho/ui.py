"""Rich-based UI helpers."""
from __future__ import annotations

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .plan_schema import JudgeReport, MasterPlan, SubagentResponse

console = Console()


def banner(title: str) -> None:
    console.print(Panel.fit(f"[bold cyan]{title}[/]"))


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


def info(msg: str) -> None:
    console.print(f"[dim]{msg}[/]")


def success(msg: str) -> None:
    console.print(f"[green]✔ {msg}[/]")


def error(msg: str) -> None:
    console.print(f"[red]✘ {msg}[/]")