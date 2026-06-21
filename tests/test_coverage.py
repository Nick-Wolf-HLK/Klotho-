"""Tests für den Coverage-Audit (Chunking, Task-Building, Orchestrierung)."""
import asyncio

from klotho import coverage
from klotho.config import SubagentConfig
from klotho.plan_schema import Finding, SubagentResponse


# --- reine Logik -----------------------------------------------------------

def test_chunk_files():
    assert coverage.chunk_files([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert coverage.chunk_files([], 3) == []
    assert coverage.chunk_files([1], 5) == [[1]]


def test_build_tasks_single_agent_default():
    chunks = [["a.py"], ["b.py"]]
    tasks = coverage.build_tasks(chunks, ["m1", "m2"])
    assert len(tasks) == 2                       # ein Voll-Audit-Agent pro Chunk
    assert all(t.lens is None for t in tasks)
    assert {t.model for t in tasks} == {"m1", "m2"}   # Modelle round-robin


def test_build_tasks_multi_lens_when_requested():
    chunks = [["a.py"], ["b.py"]]
    tasks = coverage.build_tasks(chunks, ["m1"], lenses=coverage.LENSES)
    assert len(tasks) == 2 * len(coverage.LENSES)
    lens_keys = {t.lens.key for t in tasks if t.chunk_id == 0}
    assert lens_keys == {l.key for l in coverage.LENSES}


def test_build_tasks_empty_without_models():
    assert coverage.build_tasks([["a.py"]], []) == []


def test_chunk_by_budget_respects_file_and_char_limits(tmp_path):
    for n in ("a.py", "b.py", "c.py"):
        (tmp_path / n).write_text("x = 1\n")            # je ~6 Bytes
    # max 2 Dateien/Chunk → 2 Chunks
    chunks = coverage.chunk_by_budget(["a.py", "b.py", "c.py"], str(tmp_path),
                                      max_files=2, max_chars=10_000)
    assert chunks == [["a.py", "b.py"], ["c.py"]]
    # enges Zeichen-Budget → jede Datei in eigenem Chunk
    tight = coverage.chunk_by_budget(["a.py", "b.py", "c.py"], str(tmp_path),
                                     max_files=99, max_chars=5)
    assert all(len(c) == 1 for c in tight)


# --- Orchestrierung mit gefälschtem Einspeisungs-Audit ----------------------

def _patch_audit(monkeypatch, behavior):
    """Ersetzt audit_files durch eine Fake-Coroutine."""
    async def fake(client, sub, prompt, root, files, *, timeout=None):
        return behavior(sub, files)
    monkeypatch.setattr(coverage, "audit_files", fake)


def _run(subs, root="/x", **kw):
    return asyncio.run(coverage.run_coverage_audit(
        None, subs, "find bugs", root, **kw))


def test_audit_covers_all_files_and_collects_findings(monkeypatch, tmp_path):
    for n in ("a.py", "b.py", "c.py"):
        (tmp_path / n).write_text("x = 1\n")

    def behavior(sub, files):
        return SubagentResponse(agent=sub.name, model=sub.model, response="…",
                                findings=[Finding(file=files[0], line=1,
                                                  code_quote="x = 1", issue="i")])
    _patch_audit(monkeypatch, behavior)

    subs = [SubagentConfig(name="m1", model="m1", order=1)]
    responses = _run(subs, root=str(tmp_path), chunk_size=2, max_rounds=1)
    assert responses
    assert any(r.findings for r in responses)


def test_one_call_per_chunk_no_loop(monkeypatch, tmp_path):
    for n in ("a.py", "b.py", "c.py"):
        (tmp_path / n).write_text("x = 1\n")
    calls = {"n": 0}

    def behavior(sub, files):
        calls["n"] += 1
        return SubagentResponse(agent=sub.name, model=sub.model, response="…", findings=[])
    _patch_audit(monkeypatch, behavior)

    subs = [SubagentConfig(name="m1", model="m1", order=1)]
    _run(subs, root=str(tmp_path), chunk_size=1, max_rounds=3)
    # 3 Dateien, chunk_size 1 → 3 Chunks → genau 3 Calls; keine Findings ⇒ 1 Runde
    assert calls["n"] == 3


def test_loop_until_dry_stops_when_no_new_findings(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = {"n": 0}

    def behavior(sub, files):
        calls["n"] += 1
        return SubagentResponse(agent=sub.name, model=sub.model, response="…", findings=[])
    _patch_audit(monkeypatch, behavior)

    subs = [SubagentConfig(name="m1", model="m1", order=1)]
    _run(subs, root=str(tmp_path), chunk_size=5, max_rounds=3)
    assert calls["n"] == 1                     # keine neuen Findings ⇒ nur EINE Runde
