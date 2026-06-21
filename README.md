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

## Code-Analyse → Bug-Report

Gibst du einen **Projektordner** an, fährt Klotho einen **Coverage-Audit**: Das
ganze Repo wird **garantiert** durchsucht — nicht stichprobenartig. Die komplette
Quelldateiliste wird in kleine **Chunks** aufgeteilt (nach Datei-Anzahl *und*
Zeichen-Budget); pro Chunk wird der Code **direkt eingespeist** und mit **genau
einem LLM-Call** auf alle sechs Kategorien geprüft (Sicherheit, Concurrency,
Fehlerbehandlung, Ressourcen, Validierung, Logik). Coverage ist dadurch trivial
garantiert: jede Datei steckt in genau einem Chunk. Optional laufen mehrere volle
Runden, bis **keine neuen Bugs** mehr auftauchen (loop-until-dry).

**Token-Effizienz** ist hier der Kern. Frühere Versionen ließen pro Chunk einen
*agentischen Agenten* laufen, der Dateien einzeln per Tool-Call holte — bei jedem
Call wurde der ganze wachsende Verlauf erneut gesendet (**O(n²)**, der große
Token-Fresser). Jetzt:
- **1 LLM-Call pro Chunk** statt ~20–35 — der Code wird genau einmal gesendet.
- **Kein Judge im Coverage-Modus**: Chunks sehen verschiedene Dateien, sind nicht
  vergleichbar — der (große) Judge-Call entfällt; Gleichgewichtung genügt, die
  Qualität sichern Quote-Verifikation + adversariale Stufe.
- **Adversariale Gegenprüfung = 1 Call pro Befund** (die Datei wird eingespeist,
  kein Tool-Loop).
- **429/5xx mit Backoff + Retry** (respektiert `Retry-After`), Parallelität
  gedrosselt (`concurrency`).
- **Aufwand-Schätzung + Bestätigung** vor dem Lauf („293 Dateien → N LLM-Calls").

> **Modellwahl spart am meisten Tokens.** Für die Subagenten **schlanke,
> nicht-reasoning** Modelle wählen (z. B. `gpt-oss:20b/120b`, `gemma`, `qwen`,
> `gemini-flash`). **Reasoning-/„pro"-Modelle wie `deepseek-v4-pro` erzeugen pro
> Call massig internes Nachdenken** und sind für einen Bulk-Audit extrem
> token-lastig — die spar dir für gezielte Einzelfragen auf.

Konfigurierbar unter `[coverage]` in `models.toml`: `chunk_size` + `chunk_chars`
(Größe pro Chunk), `concurrency` (gegen 429), `max_rounds` (loop-until-dry) und
`adjudicate` (`false` spart die Gegenprüfung). Mehr Recall: `chunk_size`/
`chunk_chars` runter, `max_rounds` hoch.

**Ergebnis ist ein Bug-Report, kein Plan.** Im Code-Modus geben die Subagenten
ihre **Befunde strukturiert** zurück (JSON: `Datei`, `Zeile`, Schweregrad,
Kategorie, Problem, **wörtliches Code-Zitat**, Fix). Diese werden zu **einem**
Bug-Report konsolidiert (dedupliziert, nach Schweregrad sortiert) und als
`klotho-bugreport-*.md` **gespeichert** — direkt weitergebbar an ein Fix-LLM oder
von Klotho fixbar.

**Zwei-Stufen-Verifikation gegen False Positives:**

*Stufe 1 — deterministischer Quote-Check (ohne LLM):* Das wörtliche Code-Zitat
wird in der genannten Datei gesucht. Steht es **nirgends**, ist der Befund
erfunden und wird **verworfen**; steht es an einer **anderen** Zeile, wird die
Zeilennummer **korrigiert**. Anschließend wird über `(Datei, Zitat)`
**dedupliziert** — dieselbe Quellzeile zählt nur einmal, auch wenn mehrere
Auditoren sie an unterschiedlichen Zeilen melden.

*Stufe 2 — adversariale Gegenprüfung (LLM, skeptisch):* Jeden verbleibenden
Befund prüft ein **skeptischer Reviewer** erneut gegen den echten Code (mit
denselben read-only Werkzeugen, inkl. Datenfluss über Dateigrenzen). Er fängt
die Klasse „Code stimmt, Schlussfolgerung falsch": ein Feld, das angeblich fehlt
aber existiert; ein Check, der nur scheinbar invertiert ist; ein Default-Secret,
das beim Start ersetzt wird. Klar **widerlegte** Befunde fliegen raus, mitigierte
werden **herabgestuft** — im Zweifel bleibt ein Befund erhalten (nur eindeutig
Widerlegtes wird verworfen), damit echte Funde nicht verloren gehen.

Der Report nennt offen, wie viele Behauptungen in Stufe 1 (unbelegt) und Stufe 2
(widerlegt) herausgeflogen sind. Eine **strenge Schweregrad-Rubrik** in beiden
Stufen erzwingt konservative Einstufung (im Zweifel niedriger). Build-/
Distributions-Artefakte (`dist*`, `build*`, `_internal`, …) werden vom Scan
ausgeschlossen, damit eine Kopie des Quellcodes nicht als zweite Codebasis
mitgezählt wird. Liefert ein Auditor ausnahmsweise nur Prosa statt JSON, fällt
Klotho auf die LLM-gestützte Konsolidierung zurück.

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

Während der Audit läuft, zeigt ein **Live-Dashboard** in Echtzeit den Fortschritt:
aktuelle Runde, **abgedeckte Dateien** (X/Y), erledigte Chunks, gefundene Befunde
und die Verteilung nach Kategorie — mit animiertem Klotho-Spinn-Motiv, damit klar
ist: hier wird das ganze Repo durchkämmt.

Sicherheit: Das Einlesen ist **strikt read-only und auf den Projektordner
gesandboxt** — Klotho liest nur, schreibt oder führt nie aus. Ballast (`venv*`,
`node_modules`, `dist*`, `build*`, `_internal`, `__pycache__`, …) wird gar nicht
erst eingelesen.

**Warum das skaliert:** Statt jede Datei einzeln per Tool-Call zu holen (und den
Kontext bei jedem Schritt erneut zu senden), wird pro Chunk der Code **einmal**
eingespeist und in **einem** Call analysiert. Der Kontext pro Call ist klein und
konstant (durch `chunk_chars` begrenzt) — kein quadratisches Aufblähen, kein
volllaufendes Kontextfenster. Ein riesiges Repo = einfach mehr Chunks, jeder mit
genau einem günstigen Call.

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
