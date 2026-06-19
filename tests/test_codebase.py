"""Tests für den Codebase-Scanner (Ballast-Filter, Budget, Kontextaufbau)."""
from klotho import codebase


def _write(p, content=""):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_filters_ballast(tmp_path):
    _write(tmp_path / "app/main.py", "print('hi')\n")
    _write(tmp_path / "app/util.py", "x = 1\n")
    _write(tmp_path / "venv/lib/site-packages/numpy/core.py", "garbage\n" * 50)
    _write(tmp_path / "node_modules/left-pad/index.js", "module.exports=1\n")
    _write(tmp_path / "app/__pycache__/main.cpython-311.pyc", "bytecode")
    _write(tmp_path / "dist/bundle.js", "minified")

    _, res = codebase.build_context(tmp_path, budget_tokens=10_000)
    paths = [fe.relpath for fe in res.collected]

    assert any(p.endswith("main.py") for p in paths)
    assert any(p.endswith("util.py") for p in paths)
    assert res.total_source == 2  # nur die zwei app/*.py
    assert not any(
        x in p for p in paths
        for x in ("venv", "node_modules", "dist", "pycache")
    )


def test_budget_truncates(tmp_path):
    for i in range(40):
        _write(tmp_path / f"f{i}.py", "x = 1\n" * 200)
    _, res = codebase.build_context(tmp_path, budget_tokens=500)
    assert res.truncated
    assert 0 < len(res.collected) < res.total_source
    assert res.skipped == res.total_source - len(res.collected)


def test_context_has_file_headers(tmp_path):
    _write(tmp_path / "a.py", "print(1)")
    ctx, _ = codebase.build_context(tmp_path, budget_tokens=10_000)
    assert "### FILE: a.py" in ctx


def test_skips_huge_files(tmp_path):
    _write(tmp_path / "big.py", "x\n" * 100_000)   # > MAX_FILE_BYTES
    _write(tmp_path / "small.py", "ok\n")
    _, res = codebase.build_context(tmp_path, budget_tokens=10_000)
    paths = [fe.relpath for fe in res.collected]
    assert "small.py" in paths
    assert "big.py" not in paths
