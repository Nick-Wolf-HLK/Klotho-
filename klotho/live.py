"""Live-Dashboard für die agentische Audit-Phase.

Zeigt in Echtzeit, was jeder Subagent gerade durchsucht (Datei/grep), wie viele
Dateien er schon gelesen hat und wie lange es läuft — mit einem animierten
Klotho-Spinn-Motiv, damit klar ist: hier arbeitet etwas.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_SPIN = "⣾⣽⣻⢿⡿⣟⣯⣷"          # Braille-Spinner
_THREAD_W = 60                  # Breite des laufenden Fadens


@dataclass
class _AgentState:
    name: str
    model: str
    status: str = "wartet"      # wartet | aktiv | fertig | fehler
    activity: str = "…"
    files: int = 0
    calls: int = 0


def _fmt(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    return f"{m:02d}:{s:02d}"


def _thread_line(frame: int) -> Text:
    """Ein Faden, über den eine Spindel ◍ hin- und herwandert (Bounce)."""
    span = _THREAD_W - 1
    p = frame % (2 * span)
    pos = p if p <= span else 2 * span - p          # Ping-Pong
    chars = ["─"] * _THREAD_W
    chars[pos] = "◍"
    line = Text("".join(chars), style="cyan")
    line.stylize("bold white", pos, pos + 1)
    return line


def _render(states: list[_AgentState], start: float, frame: int) -> Panel:
    spin = _SPIN[frame % len(_SPIN)]
    elapsed = time.perf_counter() - start
    active = sum(1 for s in states if s.status == "aktiv")
    done = sum(1 for s in states if s.status in ("fertig", "fehler"))
    total_files = sum(s.files for s in states)

    head = Text()
    head.append(f"{spin}  ", style="bold cyan")
    head.append("Klotho spinnt den Faden", style="bold cyan")
    head.append(
        f"    {active} aktiv · {done}/{len(states)} fertig · "
        f"{total_files} Dateien · {_fmt(elapsed)}",
        style="dim",
    )

    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(width=2)
    tbl.add_column(min_width=18)
    tbl.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
    tbl.add_column(justify="right", min_width=18)
    for s in states:
        if s.status == "aktiv":
            icon, act_style = Text(spin, style="cyan"), "white"
            act = s.activity
        elif s.status == "fertig":
            icon, act_style = Text("✓", style="bold green"), "green"
            act = "fertig"
        elif s.status == "fehler":
            icon, act_style = Text("✘", style="bold red"), "red"
            act = s.activity
        else:
            icon, act_style = Text("◌", style="dim"), "dim"
            act = "wartet …"
        tbl.add_row(
            icon,
            Text(s.name, style="bold"),
            Text(act, style=act_style),
            Text(f"{s.files} Dateien · {s.calls} ⚙", style="yellow"),
        )

    body = Group(head, Text(""), _thread_line(frame), Text(""), tbl)
    return Panel(body, border_style="cyan", title="[bold cyan]🧵 Code-Audit läuft[/]",
                 padding=(1, 3))


async def run_audit_with_dashboard(
    console: Console,
    client,
    subagents,
    prompt: str,
    *,
    refine_prompt,
    root: str,
    max_iterations: int,
):
    """Führt die agentischen Subagenten aus und zeigt dabei ein Live-Dashboard."""
    from .subagent import run_subagents_parallel

    ordered = sorted(subagents, key=lambda s: s.order)
    states = {s.name: _AgentState(s.name, s.model) for s in ordered}
    state_list = [states[s.name] for s in ordered]

    def factory(name: str):
        st = states[name]

        def cb(activity: str, files: int, calls: int) -> None:
            st.status = "aktiv"
            st.activity = activity
            st.files = files
            st.calls = calls

        return cb

    start = time.perf_counter()
    with Live(_render(state_list, start, 0), console=console,
              refresh_per_second=12, transient=False) as live:
        async def animate() -> None:
            n = 0
            while True:
                n += 1
                live.update(_render(state_list, start, n))
                await asyncio.sleep(0.08)

        anim = asyncio.create_task(animate())
        try:
            results = await run_subagents_parallel(
                client, ordered, prompt, refine_prompt=refine_prompt, root=root,
                max_iterations=max_iterations, progress_factory=factory,
            )
        finally:
            anim.cancel()

        by_name = {r.agent: r for r in results}
        for st in state_list:
            r = by_name.get(st.name)
            if r and r.error:
                st.status, st.activity = "fehler", r.error[:48]
            else:
                st.status = "fertig"
        live.update(_render(state_list, start, 0))

    return results
