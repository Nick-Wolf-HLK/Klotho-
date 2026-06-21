# Klotho

```
██╗  ██╗██╗      ██████╗ ████████╗██╗  ██╗ ██████╗
██║ ██╔╝██║     ██╔═══██╗╚══██╔══╝██║  ██║██╔═══██╗
█████╔╝ ██║     ██║   ██║   ██║   ███████║██║   ██║
██╔═██╗ ██║     ██║   ██║   ██║   ██╔══██║██║   ██║
██║  ██╗███████╗╚██████╔╝   ██║   ██║  ██║╚██████╔╝
╚═╝  ╚═╝╚══════╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝ ╚═════╝
```

**Multi-LLM Orchestrator · viele Fäden, ein Plan.**

Benannt nach **Klotho** (Κλωθώ), der Schicksalsspinnerin, die aus vielen
einzelnen Fasern *einen* Faden dreht: Klotho orchestriert mehrere LLMs (via
Ollama Cloud), lässt sie parallel Entwürfe spinnen, bewertet sie neutral und
synthetisiert daraus *einen* Masterplan — den es auf Wunsch direkt ausführt.

## What it does

```
[CLI: prompt + flags]
        │
        ▼
   Orchestrator LLM  ──(optional)──►  refined prompt
        │
        ▼  asyncio.gather (parallel)
   ┌────┬────┬────┐
   ▼    ▼    ▼
 [GLM][MiniMax][Kimi] …  → draft plans
   └────┴────┴────┘
        │
        ▼
   Judge LLM (neutral, e.g. gpt-oss:20b)
        │ scores + weights each response
        ▼
   Synthesizer LLM (orchestrator model)
        │ merges weighted responses
        ▼
   MasterPlan (Pydantic-validated JSON)
        │
        ├─ --plan-only  →  print & stop
        └─ --execute    →  Executor runs steps (cwd-locked, logged)
```

> **Rollen vs. Name:** *Klotho* ist das Produkt. *Orchestrator*, *Judge* und
> *Subagent* sind die Rollen, die die einzelnen Modelle in der Pipeline spielen.

## Install

```bash
git clone https://github.com/Nick-Wolf-HLK/Klotho-.git
cd Klotho-
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Requires a running **Ollama** daemon at `http://127.0.0.1:11434` (your existing
setup) and, for cloud models, `ollama signin`.

**Cloud-Modell-Katalog:** Klotho zieht die verfügbaren Ollama-Cloud-Modelle
direkt von ollama.com (mit korrekten Tags wie `:cloud`, `:120b-cloud`) und cacht
sie 7 Tage unter `~/.klotho/cloud_models.json`. Beim ersten interaktiven Start
wird der Katalog automatisch geladen (~2 s); manuell jederzeit per
`klotho models --refresh`. Zusätzlich werden lokal installierte Modelle und in
`~/.ollama/config.json` / `~/.opencode.json` eingetragene Modelle angezeigt.

## Usage

### Interaktiver Modus (wie Claude Code / Codex)

Einfach `klotho` ohne Argument eingeben:

```bash
klotho            # oder: orchestrator  (Alias, rückwärtskompatibel)
```

Dann fragt es dich nacheinander:
1. **Orchestrator-Modell** wählen (plant & synthetisiert)
2. **Judge-Modell** wählen (bewertet neutral)
3. **Subagenten** wählen (mehrere, Leertaste = an/abwählen)
4. **Thema/Prompt** eingeben
5. **Modus**: Plan-only oder Execute (mit/ohne Dry-Run)
6. **Refine**: Soll der Orchestrator den Prompt verfeinern?
7. Bestätigen → Pipeline läuft

Nach jeder Session: "Noch eine Session?" → Loop.

### Direktmodus (ohne Interaktion)

```bash
# produce plan only (default)
klotho run "Plan a CI/CD pipeline for a Python monorepo"

# produce + execute (full-auto, cwd-locked)
klotho run "Plan a CI/CD pipeline for a Python monorepo" --execute

# preview execution without running
klotho run "…" --execute --dry-run

# let the orchestrator LLM refine the prompt first
klotho run "…" --refine

# interactive config TUI (choose which model plays which role)
klotho config

# list all known models; --refresh lädt den Ollama-Cloud-Katalog neu
klotho models
klotho models --refresh
```

## Configuration — `models.toml`

```toml
[orchestrator]
model = "glm-5.2:cloud"
base_url = "http://127.0.0.1:11434/v1"

[judge]
model = "gpt-oss:20b"          # neutral, local, no self-bias

[[subagents]]
name = "minimax"
model = "minimax-m2.7:cloud"
order = 1

[[subagents]]
name = "kimi"
model = "kimi-2.7:cloud"
order = 2

[[subagents]]
name = "nemotron"
model = "nemotron-3-super:cloud"
order = 3

[execution]
root_lock = "."                          # executor confined to this dir
log_file = "~/.klotho/log.jsonl"         # every action logged
dry_run_default = false

[rubric]
criteria = ["completeness", "feasibility", "originality", "depth"]
```

Run `klotho config` to set roles interactively (uses `questionary`).

## Agentische Code-Analyse → Bug-Report

Gibst du einen **Projektordner** an, bekommt jeder Subagent **read-only
Werkzeuge** (`list_dir`, `read_file`, `grep`, `find_files`) und **durchsucht den
Ordner selbst** — wie ein Coding-Agent: erst auflisten/suchen, dann die
relevanten Dateien lesen, iterativ. Kein „serviertes" Code-Stück, kein
Token-Budget-Limit auf den Code — der Agent navigiert gezielt.

**Ergebnis ist ein Bug-Report, kein Plan.** Im Code-Modus geben die Subagenten
ihre **Befunde strukturiert** zurück (JSON: `Datei`, `Zeile`, Schweregrad,
Kategorie, Problem, **wörtliches Code-Zitat**, Fix). Diese werden zu **einem**
Bug-Report konsolidiert (dedupliziert, nach Schweregrad sortiert) und als
`klotho-bugreport-*.md` **gespeichert** — direkt weitergebbar an ein Fix-LLM oder
von Klotho fixbar.

**Anti-Halluzination — deterministische Verifikation:** Bevor ein Befund in den
Report kommt, prüft Klotho ihn **gegen den echten Quellcode** (ohne weiteres LLM,
daher keine Re-Halluzination): Das wörtliche Code-Zitat wird in der genannten
Datei gesucht. Steht es **nirgends**, ist der Befund erfunden und wird
**verworfen**; steht es an einer **anderen** Zeile, wird die Zeilennummer
**korrigiert**. Der Report nennt offen, wie viele unbelegte Behauptungen
herausgeflogen sind. Befunde ohne maschinell prüfbares Zitat bleiben erhalten,
werden aber als „(unbestätigt — verifizieren)" markiert; ein Befund, den mehrere
Auditoren unabhängig melden, gilt als bestätigt. Zusätzlich gibt eine **strenge
Schweregrad-Rubrik** im Agent-Prompt vor, konservativ einzustufen (im Zweifel
niedriger). Liefert ein Auditor ausnahmsweise nur Prosa statt JSON, fällt Klotho
auf die LLM-gestützte Konsolidierung zurück.

**Interaktiv** — starte Klotho einfach im Ordner deines Codes; es bietet den
aktuellen Ordner zur Analyse an (nur bestätigen):

```bash
cd /pfad/zu/deinem/code
klotho
```

**Direkt per CLI** mit explizitem Ordner:

```bash
klotho run "Erstelle einen Bugreport" --context .                 # aktueller Ordner
klotho run "Erstelle einen Bugreport" --context /pfad/zum/projekt
```

Während die Subagenten suchen, zeigt ein **Live-Dashboard** in Echtzeit, was
jeder gerade tut (welche Datei er liest/grept), wie viele Dateien er schon
gelesen hat und wie lange es läuft — mit animiertem Klotho-Spinn-Motiv, damit
klar ist: hier arbeitet etwas.

Sicherheit: Die Werkzeuge sind **strikt read-only und auf den Projektordner
gesandboxt** — Subagenten können lesen und suchen, niemals schreiben oder
ausführen. Ballast (`venv*`, `node_modules`, `dist`, `__pycache__`, …) ist für
die Werkzeuge unsichtbar.

**Managed Memory — beliebig viele Dateien:** Klothos Agent behält nicht alle
gelesenen Dateien im Kontext. Nach jedem `read_file` notiert das Modell seine
Befunde, und der **rohe Dateiinhalt wird nach wenigen Schritten aus dem Kontext
entfernt** (nur die jüngsten `KEEP_RAW_RESULTS=6` bleiben voll). Die Notizen
bleiben. So läuft das Kontextfenster **nicht** voll — ein einzelner Agent kann
*hunderte* Dateien durchgehen. Nur Zeit und Tokens begrenzen, nicht der Kontext.

**Gründlichkeit (`[agent] max_iterations`, Standard 60):** So viele Werkzeug-
Runden darf jeder Subagent machen. Höher = mehr Dateien gelesen = gründlicher,
aber langsamer. Dank Managed Memory kannst du das für riesige Repos bedenkenlos
hochsetzen (z. B. **150–300**). Jeder Report endet mit einer Fußzeile:

```
_Untersucht: 80 Dateien gelesen, 120 Werkzeug-Aufrufe in 640s._
```

Statt alles einzuspeisen, holt sich jeder Agent gezielt die Dateien, die er
braucht. (Die einfachere „Code-Einspeisung" existiert weiterhin als
Bibliotheksfunktion in `klotho/codebase.py` für kleine Repos.)

## Sprache / Language (Deutsch · English)

Beim Start fragt Klotho **„Sprache / Language?"**. Die Wahl steuert beides:
die **UI-Texte** (Menüs, Onboarding, Dashboard) *und* die **Sprache der
LLM-Outputs** — Bug-Reports, Pläne und Judge-Zusammenfassungen erscheinen auf
Deutsch oder Englisch.

## Cloud-model proxy (only if needed)

If a cloud model (e.g. `minimax-m2.7:cloud`) is **not** actually pulled into
your local Ollama, direct calls will 404. Start the shim:

```bash
python -m klotho.proxy_shim --port 11435
```

Then point Klotho at it:

```toml
[orchestrator]
base_url = "http://127.0.0.1:11435/v1"
```

The shim reads `~/.opencode.json` to know which model ids are "virtual" and
falls back to the `opencode` CLI for them.

## Safety

`--execute` is **full-auto** but confined by `root_lock` (default: cwd). Every
action is appended to `log.jsonl`. Use `--dry-run` to preview.

## Tests

```bash
pytest -q
```
