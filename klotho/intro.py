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

from . import art

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
        with console.status("[cyan]Klotho erwacht …[/]", spinner="dots"):
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
    body = (
        "Ich bin [bold cyan]Klotho[/] — ich spinne aus vielen Stimmen [italic]einen[/] Faden.\n\n"
        "[bold]So arbeiten wir zusammen:[/]\n"
        "  [cyan]1.[/] Du wählst mein [bold]Orchestrator[/]-Modell — es plant und webt am Ende alles zusammen.\n"
        "  [cyan]2.[/] Du wählst einen [bold]Judge[/] — er bewertet die Entwürfe neutral.\n"
        "  [cyan]3.[/] Du wählst mehrere [bold]Subagenten[/] — sie entwerfen parallel Pläne.\n"
        "      [dim]Mehrfachauswahl: mit der Leertaste markieren, mit Enter bestätigen.[/]\n"
        "  [cyan]4.[/] Du nennst mir dein [bold]Thema[/] — und ich biete dir an, den "
        "Code im [bold]aktuellen Ordner[/] zu analysieren (die Subagenten durchsuchen ihn selbst).\n\n"
        "[dim]Starte Klotho im Ordner deines Codes. Agentisch: read-only (list/read/grep), sandboxed; Ballast bleibt unsichtbar.[/]\n"
        "[dim]Tipp: Esc bricht jederzeit ab. Setze KLOTHO_NO_INTRO=1, um die Animation zu überspringen.[/]"
    )
    console.print()
    console.print(
        Panel(body, title="[bold cyan]Willkommen[/]", border_style="cyan", padding=(1, 2))
    )


def compact_header(console: Console) -> None:
    """Schlanker Kopf für Folge-Sessions (ohne Gesicht/Onboarding)."""
    console.print()
    for line in art.LOGO.strip("\n").splitlines():
        console.print(Text(line, style="bold cyan"), highlight=False)
    console.print(Text(art.TAGLINE, style="dim italic"), highlight=False)
