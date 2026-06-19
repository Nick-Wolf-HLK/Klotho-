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
cd /Users/dominicwolf/Desktop/Cremium
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Requires a running **Ollama** daemon at `http://127.0.0.1:11434` (your existing
setup). Cloud models (`glm-5.2:cloud`, `minimax-m2.7:cloud`, …) are read from
`~/.ollama/config.json` (`integrations.*.models`) and from `~/.opencode.json`.

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
klotho "Plan a CI/CD pipeline for a Python monorepo"

# produce + execute (full-auto, cwd-locked)
klotho "Plan a CI/CD pipeline for a Python monorepo" --execute

# preview execution without running
klotho "…" --execute --dry-run

# let the orchestrator LLM refine the prompt first
klotho "…" --refine

# interactive config TUI (choose which model plays which role)
klotho config

# list all known models (ollama integrations + opencode + local ollama)
klotho models
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
