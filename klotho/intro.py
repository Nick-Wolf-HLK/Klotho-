"""Start-Sequenz für Klotho: Öffnungs-Animation, Onboarding, kompakter Header.

Die Animation enthüllt zuerst Klothos Gesicht, dann das Logo — wie ein sich
öffnender Vorhang. Sie wird automatisch übersprungen, wenn kein TTY vorliegt
(Pipe/CI) oder die Umgebungsvariable KLOTHO_NO_INTRO gesetzt ist.
"""
from __future__ import annotations

import os
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import art, i18n

# Zeiten bewusst kurz halten — beeindrucken, nicht aufhalten.
_FACE_DELAY = 0.022
_LOGO_DELAY = 0.06


def _animate() -> bool:
    if os.environ.get("KLOTHO_NO_INTRO"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def play_intro(console: Console) -> None:
    """Öffnungssequenz: Gesicht enthüllt sich, Logo erscheint, Tagline."""
    animate = _animate()
    console.clear()

    if animate:
        with console.status(i18n.t("[cyan]Klotho erwacht …[/]", "[cyan]Klotho awakens …[/]"),
                            spinner="dots"):
            time.sleep(0.7)

    for line in art.FACE.strip("\n").splitlines():
        console.print(Text(line, style="cyan"), highlight=False)
        if animate:
            time.sleep(_FACE_DELAY)

    console.print()
    for line in art.LOGO.strip("\n").splitlines():
        console.print(Text(line, style="bold cyan"), highlight=False)
        if animate:
            time.sleep(_LOGO_DELAY)

    console.print(Text(art.TAGLINE, style="dim italic"), highlight=False)
    if animate:
        time.sleep(0.3)


def show_onboarding(console: Console) -> None:
    """Erklärt freundlich, wie Klotho funktioniert."""
    body = i18n.t(
        de=(
            "Ich bin [bold cyan]Klotho[/] — ich spinne aus vielen Stimmen [italic]einen[/] Faden.\n\n"
            "[bold]So arbeiten wir zusammen:[/]\n"
            "  [cyan]1.[/] Du wählst mein [bold]Orchestrator[/]-Modell — es plant und webt am Ende alles zusammen.\n"
            "  [cyan]2.[/] Du wählst einen [bold]Judge[/] — er bewertet die Entwürfe neutral.\n"
            "  [cyan]3.[/] Du wählst mehrere [bold]Subagenten[/] — sie arbeiten parallel.\n"
            "      [dim]Mehrfachauswahl: mit der Leertaste markieren, mit Enter bestätigen.[/]\n"
            "  [cyan]4.[/] Du nennst mir dein [bold]Thema[/] — und ich biete dir an, den "
            "Code im [bold]aktuellen Ordner[/] zu analysieren (die Subagenten durchsuchen ihn selbst).\n\n"
            "[dim]Starte Klotho im Ordner deines Codes. Agentisch: read-only (list/read/grep), sandboxed; Ballast bleibt unsichtbar.[/]\n"
            "[dim]Tipp: Esc bricht jederzeit ab. Setze KLOTHO_NO_INTRO=1, um die Animation zu überspringen.[/]"
        ),
        en=(
            "I am [bold cyan]Klotho[/] — I spin [italic]one[/] thread from many voices.\n\n"
            "[bold]Here's how we work together:[/]\n"
            "  [cyan]1.[/] You pick my [bold]Orchestrator[/] model — it plans and weaves everything together.\n"
            "  [cyan]2.[/] You pick a [bold]Judge[/] — it rates the drafts neutrally.\n"
            "  [cyan]3.[/] You pick several [bold]Subagents[/] — they work in parallel.\n"
            "      [dim]Multi-select: mark with space, confirm with Enter.[/]\n"
            "  [cyan]4.[/] You give me your [bold]topic[/] — and I offer to analyse the code in the "
            "[bold]current folder[/] (the subagents explore it themselves).\n\n"
            "[dim]Start Klotho inside your code folder. Agentic: read-only (list/read/grep), sandboxed; ballast stays invisible.[/]\n"
            "[dim]Tip: Esc cancels anytime. Set KLOTHO_NO_INTRO=1 to skip the animation.[/]"
        ),
    )
    console.print()
    console.print(
        Panel(body, title=i18n.t("[bold cyan]Willkommen[/]", "[bold cyan]Welcome[/]"),
              border_style="cyan", padding=(1, 2))
    )


def compact_header(console: Console) -> None:
    """Schlanker Kopf für Folge-Sessions (ohne Gesicht/Onboarding)."""
    console.print()
    for line in art.LOGO.strip("\n").splitlines():
        console.print(Text(line, style="bold cyan"), highlight=False)
    console.print(Text(art.TAGLINE, style="dim italic"), highlight=False)
