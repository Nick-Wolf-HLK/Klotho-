"""Coverage-Audit: durchsucht GARANTIERT das ganze Repo — token-effizient.

Die komplette Quelldateiliste wird in Chunks aufgeteilt (nach Datei-Anzahl UND
Zeichen-Budget). Pro Chunk wird der Code DIREKT EINGESPEIST und mit GENAU EINEM
LLM-Call analysiert — kein agentischer Tool-Loop (der pro Chunk Dutzende Calls
mit wachsendem Kontext bräuchte). Coverage ist dadurch trivial garantiert: jede
Datei steckt in genau einem Chunk und wird eingespeist.

Das Ergebnis ist eine Liste von SubagentResponse — kompatibel mit der bestehenden
Pipeline (Quote-Verifikation → adversariale Gegenprüfung → Bug-Report).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from . import codebase
from .agent import audit_files
from .config import SubagentConfig
from .llm_client import LLMClient
from .plan_schema import Finding, SubagentResponse

# Default-Parameter — auf EFFIZIENZ getrimmt: ein Einspeisungs-Call pro Chunk,
# niedrige Parallelität (gegen Rate-Limit), eine Runde. In models.toml [coverage].
DEFAULT_CHUNK_SIZE = 6           # Dateien pro Chunk (Obergrenze)
DEFAULT_CHUNK_CHARS = 24_000     # Zeichen-Budget pro Chunk (eingespeister Code)
DEFAULT_MAX_CONCURRENCY = 3
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


def chunk_by_budget(
    files: list[str],
    root: str,
    *,
    max_files: int = DEFAULT_CHUNK_SIZE,
    max_chars: int = DEFAULT_CHUNK_CHARS,
) -> list[list[str]]:
    """Gruppiert Dateien in Chunks, begrenzt durch Datei-ANZAHL UND Zeichen-Budget.
    So bleibt der eingespeiste Code pro Chunk klein genug für EINEN Call; sehr
    große Dateien landen allein in ihrem Chunk."""
    root_p = root
    chunks: list[list[str]] = []
    cur: list[str] = []
    cur_chars = 0
    for f in files:
        try:
            size = os.path.getsize(os.path.join(root_p, f))
        except OSError:
            size = 0
        if cur and (len(cur) >= max_files or cur_chars + size > max_chars):
            chunks.append(cur)
            cur, cur_chars = [], 0
        cur.append(f)
        cur_chars += size
    if cur:
        chunks.append(cur)
    return chunks


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
    files: list[str],
    root: str,
    *,
    max_files: int = DEFAULT_CHUNK_SIZE,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> dict:
    """Vorab-Schätzung des Aufwands (für eine Bestätigung VOR dem Lauf). Im
    Einspeisungs-Modus = 1 LLM-Call pro Chunk pro Runde."""
    n_chunks = len(chunk_by_budget(files, root, max_files=max_files, max_chars=max_chars)) if files else 0
    return {
        "files": len(files),
        "chunks": n_chunks,
        "calls_per_round": n_chunks,
        "calls_total": n_chunks * max(1, max_rounds),
    }


def _finding_key(f: Finding) -> tuple[str, str]:
    from . import verify
    return (os.path.normpath(f.file or ""), verify._norm(f.code_quote) or f"L{f.line}")


async def _run_task(
    client: LLMClient,
    task: Task,
    prompt: str,
    root: str,
    *,
    timeout,
    sem: asyncio.Semaphore,
    state: CoverageState,
    on_update,
) -> SubagentResponse:
    sub = SubagentConfig(name=task.name, model=task.model, order=task.chunk_id)
    state.activity[task.name] = f"{len(task.files)} Dateien"
    if on_update:
        on_update(state)
    async with sem:
        resp = await audit_files(client, sub, prompt, root, task.files, timeout=timeout)
    state.tasks_done += 1
    state.files_covered += len(task.files)        # Einspeisung deckt jede Chunk-Datei ab
    state.activity.pop(task.name, None)
    if resp.findings:
        state.findings += len(resp.findings)
        for f in resp.findings:
            cat = f.category or "?"
            state.lens_counts[cat] = state.lens_counts.get(cat, 0) + 1
    if on_update:
        on_update(state)
    return resp


async def run_coverage_audit(
    client: LLMClient,
    subagents: list[SubagentConfig],
    prompt: str,
    root: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    timeout=None,
    on_update=None,
) -> list[SubagentResponse]:
    """Coverage-Audit per direkter Code-Einspeisung: ein LLM-Call pro Chunk.
    Coverage ist garantiert (jede Datei ist in genau einem Chunk und wird
    eingespeist). Liefert alle SubagentResponses für die nachgelagerte Pipeline."""
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
    chunks = chunk_by_budget(files, root, max_files=chunk_size, max_chars=chunk_chars)

    for rnd in range(1, max_rounds + 1):
        state.round = rnd
        new_this_round = 0
        tasks = build_tasks(chunks, models, round_no=rnd)
        state.tasks_total = len(tasks)
        state.tasks_done = 0
        state.files_covered = 0
        if on_update:
            on_update(state)

        results = await asyncio.gather(*(
            _run_task(client, t, prompt, root, timeout=timeout,
                      sem=sem, state=state, on_update=on_update)
            for t in tasks
        ))
        for resp in results:
            all_responses.append(resp)
            for f in resp.findings:
                k = _finding_key(f)
                if k not in seen_keys:
                    seen_keys.add(k)
                    new_this_round += 1
        if on_update:
            on_update(state)

        # Loop-until-dry: keine NEUEN Befunde mehr → fertig
        if new_this_round == 0:
            break

    return all_responses
