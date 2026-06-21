"""Coverage-Audit: durchsucht GARANTIERT das ganze Repo, aus mehreren Blickwinkeln.

Statt die Subagenten frei navigieren zu lassen (und Dateien zu übersehen), wird
die komplette Quelldateiliste in Chunks aufgeteilt und jeder Chunk × jede
Analyse-Lens einem Agenten zugewiesen, der seine Dateien GARANTIERT liest. Nicht
gelesene Dateien werden in Nachrunden neu zugewiesen, bis das ganze Repo
abgedeckt ist ("spawnt, bis alles durchsucht ist"). Optional laufen mehrere volle
Runden, bis keine neuen Befunde mehr auftauchen (loop-until-dry).

Das Ergebnis ist eine Liste von SubagentResponse — kompatibel mit der bestehenden
Pipeline (Judge → Quote-Verifikation → adversariale Gegenprüfung → Bug-Report).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from . import codebase
from .agent import run_agentic_subagent
from .config import SubagentConfig
from .llm_client import LLMClient
from .plan_schema import Finding, SubagentResponse

# Default-Parameter — auf EFFIZIENZ getrimmt: EIN Agent pro Chunk (prüft alle
# Kategorien via Checkliste), niedrige Parallelität (gegen Rate-Limit), eine
# Runde. Alles in models.toml [coverage] überschreibbar.
DEFAULT_CHUNK_SIZE = 10
DEFAULT_MAX_CONCURRENCY = 3
DEFAULT_MAX_ITERATIONS = 40
DEFAULT_MAX_ROUNDS = 1
DEFAULT_COVERAGE_RETRIES = 1


@dataclass(frozen=True)
class Lens:
    key: str
    de: str
    en: str
    focus: str


LENSES: list[Lens] = [
    Lens("security", "Sicherheit", "Security",
         "security holes: injection (SQL/command/template), auth/authorization gaps, secrets in "
         "code, path traversal, unsafe deserialization/eval, SSRF, missing crypto/weak hashing."),
    Lens("concurrency", "Concurrency", "Concurrency",
         "concurrency bugs: race conditions, shared mutable state without locks, async/await misuse "
         "(blocking calls in async, missing await), deadlocks, non-atomic read-modify-write."),
    Lens("errors", "Fehlerbehandlung", "Error handling",
         "error handling: unhandled exceptions, swallowed/broad excepts, missing None/empty checks, "
         "unchecked return values, errors that leave state inconsistent."),
    Lens("resources", "Ressourcen", "Resources",
         "resource & performance bugs: leaked files/sockets/connections, missing cleanup/close, "
         "unbounded memory or input (OOM), N+1 / quadratic loops, missing size limits."),
    Lens("validation", "Validierung", "Input validation",
         "input validation & boundaries: missing validation of external input, off-by-one, boundary "
         "/empty/overflow conditions, type confusion, unchecked indexes/keys."),
    Lens("logic", "Logik", "Logic",
         "logic correctness: inverted/incorrect conditions, wrong operators, mismatched units, "
         "contract violations, dead/unreachable code, copy-paste mistakes."),
]


@dataclass
class Task:
    chunk_id: int
    files: list[str]
    lens: Lens | None        # None = Voll-Audit (alle Kategorien via Checkliste)
    model: str
    name: str


@dataclass
class CoverageState:
    """Aggregierter Live-Status für das Dashboard (wird in-place mutiert)."""
    round: int = 0
    max_rounds: int = 0
    tasks_done: int = 0
    tasks_total: int = 0
    files_total: int = 0
    files_covered: int = 0
    findings: int = 0
    activity: dict[str, str] = field(default_factory=dict)   # task_name -> aktuelle Aktion
    lens_counts: dict[str, int] = field(default_factory=dict)  # lens_key -> #findings


def chunk_files(files: list[str], size: int) -> list[list[str]]:
    """Teilt die Dateiliste in Chunks fester Größe (letzter Chunk kann kleiner sein)."""
    size = max(1, size)
    return [files[i:i + size] for i in range(0, len(files), size)]


def build_tasks(
    chunks: list[list[str]],
    models: list[str],
    lenses: list[Lens] | None = None,
    *,
    round_no: int = 1,
) -> list[Task]:
    """Erzeugt Tasks; Modelle werden round-robin (pro Runde rotiert) verteilt.

    Standard (``lenses=None``): EIN Voll-Audit-Agent pro Chunk, der alle
    Kategorien per Checkliste prüft — effizient. Wird eine Lens-Liste übergeben,
    entsteht ein Task pro (Chunk × Lens) — gründlicher, aber teurer."""
    if not models:
        return []
    lens_cycle = lenses if lenses else [None]
    tasks: list[Task] = []
    i = 0
    for c_idx, chunk in enumerate(chunks):
        for lens in lens_cycle:
            model = models[(i + round_no - 1) % len(models)]
            short = model.split(":")[0]
            tag = f"·{lens.key}" if lens else ""
            tasks.append(Task(
                chunk_id=c_idx,
                files=chunk,
                lens=lens,
                model=model,
                name=f"c{c_idx}{tag}·{short}",
            ))
            i += 1
    return tasks


def estimate_audit(
    n_files: int,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    lenses: list[Lens] | None = None,
) -> dict:
    """Grobe Vorab-Schätzung des Aufwands (für eine Bestätigung VOR dem Lauf)."""
    n_chunks = (n_files + chunk_size - 1) // max(1, chunk_size) if n_files else 0
    per_round = n_chunks * (len(lenses) if lenses else 1)
    return {
        "files": n_files,
        "chunks": n_chunks,
        "agents_per_round": per_round,
        "agents_total": per_round * max(1, max_rounds),
    }


def _norm_paths(paths) -> set[str]:
    return {os.path.normpath(p) for p in paths if p}


def _finding_key(f: Finding) -> tuple[str, str]:
    from . import verify
    return (os.path.normpath(f.file or ""), verify._norm(f.code_quote) or f"L{f.line}")


async def _run_task(
    client: LLMClient,
    task: Task,
    prompt: str,
    root: str,
    *,
    max_iterations: int,
    timeout,
    sem: asyncio.Semaphore,
    state: CoverageState,
    on_update,
) -> tuple[SubagentResponse, set[str]]:
    read_sink: set[str] = set()
    sub = SubagentConfig(name=task.name, model=task.model, order=task.chunk_id)

    def _progress(activity: str, files: int, calls: int) -> None:
        state.activity[task.name] = activity
        if on_update:
            on_update(state)

    async with sem:
        resp = await run_agentic_subagent(
            client, sub, prompt, root,
            max_iterations=max_iterations, timeout=timeout, progress=_progress,
            lens=task.lens.focus if task.lens else None,
            assigned_files=task.files, read_sink=read_sink,
        )
    state.tasks_done += 1
    state.activity.pop(task.name, None)
    if resp.findings:
        state.findings += len(resp.findings)
        # nach Befund-Kategorie zählen (funktioniert auch im Voll-Audit ohne Lens)
        for f in resp.findings:
            cat = f.category or "?"
            state.lens_counts[cat] = state.lens_counts.get(cat, 0) + 1
    if on_update:
        on_update(state)
    return resp, read_sink


async def run_coverage_audit(
    client: LLMClient,
    subagents: list[SubagentConfig],
    prompt: str,
    root: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    coverage_retries: int = DEFAULT_COVERAGE_RETRIES,
    timeout=None,
    on_update=None,
) -> list[SubagentResponse]:
    """Selbstskalierender Coverage-Audit. Liefert alle SubagentResponses aller
    Runden (mit strukturierten Findings) für die nachgelagerte Pipeline."""
    files = codebase.collect_source_files(root)
    models = [s.model for s in sorted(subagents, key=lambda s: s.order)] or ["gpt-oss:20b"]
    state = CoverageState(files_total=len(files), max_rounds=max_rounds)
    if on_update:
        on_update(state)
    if not files:
        return []

    sem = asyncio.Semaphore(max_concurrency)
    all_responses: list[SubagentResponse] = []
    seen_keys: set[tuple[str, str]] = set()
    covered: set[str] = set()
    target = _norm_paths(files)

    for rnd in range(1, max_rounds + 1):
        state.round = rnd
        new_this_round = 0

        # --- Volle Coverage dieser Runde: ein Voll-Audit-Agent pro Chunk ---
        chunks = chunk_files(files, chunk_size)
        tasks = build_tasks(chunks, models, round_no=rnd)
        state.tasks_total = len(tasks)
        state.tasks_done = 0
        if on_update:
            on_update(state)

        results = await asyncio.gather(*(
            _run_task(client, t, prompt, root, max_iterations=max_iterations,
                      timeout=timeout, sem=sem, state=state, on_update=on_update)
            for t in tasks
        ))
        for resp, sink in results:
            all_responses.append(resp)
            covered |= _norm_paths(sink)
            for f in resp.findings:
                k = _finding_key(f)
                if k not in seen_keys:
                    seen_keys.add(k)
                    new_this_round += 1
        state.files_covered = len(covered & target)
        if on_update:
            on_update(state)

        # --- Nachrunden: nicht gelesene Dateien gezielt neu zuweisen ---
        for _ in range(coverage_retries):
            missing = sorted(f for f in files if os.path.normpath(f) not in covered)
            if not missing:
                break
            mchunks = chunk_files(missing, chunk_size)
            mtasks = build_tasks(mchunks, models, round_no=rnd)   # Voll-Audit der Lücke
            state.tasks_total += len(mtasks)
            if on_update:
                on_update(state)
            mres = await asyncio.gather(*(
                _run_task(client, t, prompt, root, max_iterations=max_iterations,
                          timeout=timeout, sem=sem, state=state, on_update=on_update)
                for t in mtasks
            ))
            for resp, sink in mres:
                all_responses.append(resp)
                covered |= _norm_paths(sink)
                for f in resp.findings:
                    k = _finding_key(f)
                    if k not in seen_keys:
                        seen_keys.add(k)
                        new_this_round += 1
            state.files_covered = len(covered & target)
            if on_update:
                on_update(state)

        # --- Loop-until-dry: keine NEUEN Befunde mehr → fertig ---
        if new_this_round == 0:
            break

    return all_responses
