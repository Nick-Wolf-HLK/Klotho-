"""Tests for the executor (dry-run + path lock + bash)."""
from pathlib import Path

from klotho.executor import ExecutionError, Executor
from klotho.plan_schema import MasterPlan, PlanStep


def _plan(tmp_path: Path) -> MasterPlan:
    return MasterPlan(
        title="t",
        summary="s",
        steps=[
            PlanStep(id=1, title="bash", description="x", action="bash", command="echo hi"),
            PlanStep(
                id=2,
                title="write",
                description="hello",
                action="write_file",
                path="out.txt",
                depends_on=[1],
            ),
        ],
    )


def test_executor_dry_run(tmp_path: Path):
    ex = Executor(root_lock=str(tmp_path), log_file=str(tmp_path / "log.jsonl"), dry_run=True)
    results = ex.execute(_plan(tmp_path))
    assert len(results) == 2
    assert all(r["ok"] for r in results)
    assert not (tmp_path / "out.txt").exists()


def test_executor_runs(tmp_path: Path):
    ex = Executor(root_lock=str(tmp_path), log_file=str(tmp_path / "log.jsonl"))
    results = ex.execute(_plan(tmp_path))
    assert results[0]["ok"] and results[1]["ok"]
    assert (tmp_path / "out.txt").read_text() == "hello"


def test_executor_blocks_outside_root(tmp_path: Path):
    ex = Executor(root_lock=str(tmp_path), log_file=str(tmp_path / "log.jsonl"))
    plan = MasterPlan(
        title="t",
        summary="s",
        steps=[
            PlanStep(
                id=1,
                title="escape",
                description="x",
                action="write_file",
                path="../../etc/bad.txt",
            )
        ],
    )
    try:
        ex.execute(plan)
        assert False, "expected ExecutionError"
    except ExecutionError:
        pass


def test_executor_unresolvable_deps(tmp_path: Path):
    ex = Executor(root_lock=str(tmp_path), log_file=str(tmp_path / "log.jsonl"))
    plan = MasterPlan(
        title="t",
        summary="s",
        steps=[PlanStep(id=1, title="x", description="x", action="noop", depends_on=[99])],
    )
    try:
        ex.execute(plan)
        assert False, "expected ExecutionError"
    except ExecutionError:
        pass