"""Tests für die read-only Code-Werkzeuge (inkl. Sandbox)."""
from klotho.tools import CodeTools


def _write(p, content=""):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_read_file_with_line_numbers(tmp_path):
    _write(tmp_path / "app/main.py", "import os\nx = 1\n")
    t = CodeTools(tmp_path)
    out = t.read_file("app/main.py")
    assert "1: import os" in out
    assert "2: x = 1" in out


def test_sandbox_blocks_outside_root(tmp_path):
    t = CodeTools(tmp_path)
    out = t.dispatch("read_file", {"path": "../../../../etc/passwd"})
    assert "außerhalb" in out or "Fehler" in out  # nicht gelesen


def test_grep_finds_pattern(tmp_path):
    _write(tmp_path / "a.py", "def foo():\n    return 1\n")
    _write(tmp_path / "b.py", "def bar():\n    pass\n")
    t = CodeTools(tmp_path)
    out = t.grep("def foo")
    assert "a.py:1" in out
    assert "b.py" not in out


def test_list_dir_hides_ballast(tmp_path):
    _write(tmp_path / "app/main.py", "x")
    _write(tmp_path / "venv/lib/x.py", "y")
    t = CodeTools(tmp_path)
    out = t.list_dir(".")
    assert "app/" in out
    assert "venv" not in out


def test_grep_skips_ballast(tmp_path):
    _write(tmp_path / "app/main.py", "SECRET_TOKEN = 1\n")
    _write(tmp_path / "node_modules/pkg/index.js", "SECRET_TOKEN = 2\n")
    t = CodeTools(tmp_path)
    out = t.grep("SECRET_TOKEN")
    assert "app/main.py" in out
    assert "node_modules" not in out


def test_find_files_glob(tmp_path):
    _write(tmp_path / "app/service.py", "x")
    _write(tmp_path / "app/util.py", "y")
    t = CodeTools(tmp_path)
    out = t.find_files("*service*")
    assert "service.py" in out
    assert "util.py" not in out


def test_dispatch_unknown_tool(tmp_path):
    t = CodeTools(tmp_path)
    assert "unbekannt" in t.dispatch("delete_everything", {})
