"""Tests für den Codebase-Scanner (Ballast-Filter, Quelldatei-Auflistung)."""
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

    paths = codebase.collect_source_files(tmp_path)

    assert any(p.endswith("main.py") for p in paths)
    assert any(p.endswith("util.py") for p in paths)
    assert len(paths) == 2  # nur die zwei app/*.py
    assert not any(
        x in p for p in paths
        for x in ("venv", "node_modules", "dist", "pycache")
    )


def test_skips_huge_files(tmp_path):
    _write(tmp_path / "big.py", "x\n" * 100_000)   # > MAX_FILE_BYTES
    _write(tmp_path / "small.py", "ok\n")
    paths = codebase.collect_source_files(tmp_path)
    assert "small.py" in paths
    assert "big.py" not in paths


def test_skips_empty_files(tmp_path):
    _write(tmp_path / "empty.py", "")
    _write(tmp_path / "real.py", "x = 1\n")
    paths = codebase.collect_source_files(tmp_path)
    assert "real.py" in paths
    assert "empty.py" not in paths
