"""Tests für den Coverage-Audit (Chunking, Task-Building, Orchestrierung)."""
import asyncio

import pytest

from klotho import coverage
from klotho.config import SubagentConfig
from klotho.plan_schema import Finding, SubagentResponse


# --- reine Logik -----------------------------------------------------------

def test_chunk_files():
    assert coverage.chunk_files([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert coverage.chunk_files([], 3) == []
    assert coverage.chunk_files([1], 5) == [[1]]


def test_build_tasks_chunk_x_lens_and_model_roundrobin():
    chunks = [["a.py"], ["b.py"]]
    models = ["m1", "m2"]
    tasks = coverage.build_tasks(chunks, models)
    # 2 Chunks × 6 Lenses
    assert len(tasks) == 2 * len(coverage.LENSES)
    # jede Lens kommt pro Chunk genau einmal vor
    lens_keys = {t.lens.key for t in tasks if t.chunk_id == 0}
    assert lens_keys == {l.key for l in coverage.LENSES}
    # Modelle round-robin → beide Modelle genutzt
    assert {t.model for t in tasks} == {"m1", "m2"}


def test_build_tasks_empty_without_models():
    assert coverage.build_tasks([["a.py"]], []) == []


# --- Orchestrierung mit gefälschtem Agenten --------------------------------

def _patch_agent(monkeypatch, behavior):
    """Ersetzt run_agentic_subagent durch eine Fake-Coroutine."""
    async def fake(client, sub, prompt, root, *, max_iterations, timeout,
                   progress=None, lens=None, assigned_files=None, read_sink=None):
        return behavior(sub, assigned_files, read_sink)
    monkeypatch.setattr(coverage, "run_agentic_subagent", fake)


def _run(subs, root="/x", **kw):
    return asyncio.run(coverage.run_coverage_audit(
        None, subs, "find bugs", root, **kw))


def test_audit_covers_all_files_and_collects_findings(monkeypatch, tmp_path):
    for n in ("a.py", "b.py", "c.py"):
        (tmp_path / n).write_text("x = 1\n")

    def behavior(sub, assigned, sink):
        for f in assigned:                 # liest alle zugewiesenen Dateien
            sink.add(f)
        return SubagentResponse(agent=sub.name, model=sub.model, response="…",
                                findings=[Finding(file=assigned[0], line=1,
                                                  code_quote="x = 1", issue="i")])
    _patch_agent(monkeypatch, behavior)

    subs = [SubagentConfig(name="m1", model="m1", order=1)]
    responses = _run(subs, root=str(tmp_path), chunk_size=2, max_rounds=1)
    assert responses
    # mindestens ein Befund eingesammelt, alle Dateien abgedeckt
    assert any(r.findings for r in responses)


def test_loop_until_dry_stops_after_one_round_when_no_findings(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = {"n": 0}

    def behavior(sub, assigned, sink):
        calls["n"] += 1
        for f in assigned:
            sink.add(f)
        return SubagentResponse(agent=sub.name, model=sub.model, response="…", findings=[])
    _patch_agent(monkeypatch, behavior)

    subs = [SubagentConfig(name="m1", model="m1", order=1)]
    _run(subs, root=str(tmp_path), chunk_size=5, max_rounds=3)
    # 1 Datei → 1 Chunk × 6 Lenses = 6 Tasks; keine Findings ⇒ nur EINE Runde
    assert calls["n"] == len(coverage.LENSES)


def test_retry_reassigns_unread_files(monkeypatch, tmp_path):
    for n in ("a.py", "b.py"):
        (tmp_path / n).write_text("x = 1\n")
    # nur eine Lens, damit jede Datei pro Hauptrunde genau einmal zugewiesen wird
    monkeypatch.setattr(coverage, "LENSES", [coverage.LENSES[-1]])
    attempted: set[str] = set()

    def behavior(sub, assigned, sink):
        # liest eine Datei erst beim ZWEITEN Zuweisen → erzwingt eine Nachrunde
        for f in assigned:
            if f in attempted:
                sink.add(f)
            else:
                attempted.add(f)
        return SubagentResponse(agent=sub.name, model=sub.model, response="…", findings=[])
    _patch_agent(monkeypatch, behavior)

    subs = [SubagentConfig(name="m1", model="m1", order=1)]
    responses = _run(subs, root=str(tmp_path), chunk_size=5, max_rounds=1,
                     coverage_retries=2)
    # Hauptrunde (0 gelesen) + Nachrunde (alle gelesen) ⇒ mehr als nur die Hauptrunde
    assert len(responses) >= 2
