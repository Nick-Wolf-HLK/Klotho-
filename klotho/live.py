"""Live-Dashboard für den Coverage-Audit.

Zeigt in Echtzeit, wie Klotho das ganze Repo durchkämmt: aktuelle Runde,
abgedeckte Dateien, erledigte Tasks, gefundene Befunde und welche Analyse-Lens
gerade was findet — mit einem animierten Spinn-Motiv, damit klar ist: hier
arbeitet etwas Großes.
"""
from __future__ import annotations

import asyncio
import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import i18n
from .coverage import LENSES

_SPIN = "⣾⣽⣻⢿⡿⣟⣯⣷"          # Braille-Spinner
_THREAD_W = 58
_BAR_W = 32


def _fmt(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    return f"{m:02d}:{s:02d}"


def _thread_line(frame: int) -> Text:
    span = _THREAD_W - 1
    p = frame % (2 * span)
    pos = p if p <= span else 2 * span - p
    chars = ["─"] * _THREAD_W
    chars[pos] = "◍"
    line = Text("".join(chars), style="cyan")
    line.stylize("bold white", pos, pos + 1)
    return line


def _bar(done: int, total: int, color: str) -> Text:
    frac = (done / total) if total else 0.0
    filled = int(round(frac * _BAR_W))
    t = Text()
    t.append("█" * filled, style=color)
    t.append("░" * (_BAR_W - filled), style="dim")
    t.append(f"  {done}/{total}", style="bold")
    return t


def _render(state, start: float, frame: int) -> Panel:
    spin = _SPIN[frame % len(_SPIN)]
    elapsed = time.perf_counter() - start

    head = Text()
    head.append(f"{spin}  ", style="bold cyan")
    head.append(i18n.t("Klotho durchkämmt das Repo", "Klotho is combing the repo"), style="bold cyan")
    if state is not None:
        head.append(
            f"    {i18n.t('Runde', 'round')} {max(1, state.round)}/{state.max_rounds}"
            f"  ·  {_fmt(elapsed)}",
            style="dim",
        )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="bold")
    grid.add_column()
    if state is not None:
        grid.add_row(i18n.t("Dateien", "files"), _bar(state.files_covered, state.files_total, "green"))
        grid.add_row(i18n.t("Tasks", "tasks"), _bar(state.tasks_done, state.tasks_total, "cyan"))
        grid.add_row(i18n.t("Befunde", "findings"),
                     Text(str(state.findings), style="bold yellow"))

    # Lens-Verteilung
    lens_line = Text()
    if state is not None and state.lens_counts:
        for lens in LENSES:
            n = state.lens_counts.get(lens.key, 0)
            if n:
                lens_line.append(f"{i18n.t(lens.de, lens.en)} ", style="magenta")
                lens_line.append(f"{n}  ", style="bold")

    # Aktuell laufende Tasks (ein paar zeigen)
    act = Table.grid(padding=(0, 2))
    act.add_column(width=2)
    act.add_column(min_width=20)
    act.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
    if state is not None:
        for name, activity in list(state.activity.items())[:8]:
            act.add_row(Text(spin, style="cyan"), Text(name, style="bold"),
                        Text(activity, style="white"))

    parts = [head, Text(""), _thread_line(frame), Text(""), grid]
    if lens_line.plain:
        parts += [Text(""), lens_line]
    parts += [Text(""), act]
    return Panel(Group(*parts), border_style="cyan",
                 title=i18n.t("[bold cyan]🧵 Code-Audit läuft[/]", "[bold cyan]🧵 Code audit running[/]"),
                 padding=(1, 3))


async def run_coverage_with_dashboard(
    console: Console,
    client,
    subagents,
    prompt: str,
    *,
    root: str,
    chunk_size: int,
    max_concurrency: int,
    max_iterations: int,
    max_rounds: int,
):
    """Führt den Coverage-Audit aus und zeigt dabei das Live-Dashboard."""
    from . import coverage

    latest = {"state": None}

    def on_update(st) -> None:
        latest["state"] = st

    start = time.perf_counter()
    with Live(_render(None, start, 0), console=console,
              refresh_per_second=12, transient=False) as live:
        async def animate() -> None:
            n = 0
            while True:
                n += 1
                live.update(_render(latest["state"], start, n))
                await asyncio.sleep(0.08)

        anim = asyncio.create_task(animate())
        try:
            responses = await coverage.run_coverage_audit(
                client, subagents, prompt, root,
                chunk_size=chunk_size, max_concurrency=max_concurrency,
                max_iterations=max_iterations, max_rounds=max_rounds,
                on_update=on_update,
            )
        finally:
            anim.cancel()
        live.update(_render(latest["state"], start, 0))

    return responses
