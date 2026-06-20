"""Codebase-Scanner: liest den echten Quellcode eines Ordners ein und baut einen
Kontext-String für die Subagenten — unter einem Token-Budget.

Filtert aggressiv Ballast (virtuelle Umgebungen, eingebettete Interpreter,
node_modules, Builds, Caches, Binär-/Lock-Dateien, Riesendateien), damit auch
in scheinbar gigantischen Repos nur echter Quellcode eingespeist wird.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIR_PATTERNS = (
    ".git", ".hg", ".svn",
    "venv", "venv-*", "*-venv", ".venv", "env", ".env", "virtualenv",
    "py3*", "python3*", "site-packages",          # eingebettete Interpreter/Libs
    "node_modules", "bower_components", "vendor", "Pods",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
    "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".gradle",
    "*.egg-info", ".tox", ".idea", ".vscode", ".DS_Store",
)

SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".scala", ".rb", ".php", ".swift",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs",
    ".sh", ".bash", ".sql", ".lua", ".dart",
}
EXTRA_FILENAMES = {"dockerfile", "makefile"}

MAX_FILE_BYTES = 80_000          # generierte/minifizierte Riesendateien überspringen
DEFAULT_BUDGET_TOKENS = 60_000


def _dir_ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in IGNORE_DIR_PATTERNS)


def _is_source(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_EXTS or path.name.lower() in EXTRA_FILENAMES


@dataclass
class FileEntry:
    relpath: str
    size: int


@dataclass
class ScanResult:
    root: str
    collected: list = field(default_factory=list)   # eingespeiste FileEntry
    total_source: int = 0                            # echte Quelldateien gesamt
    tokens: int = 0                                  # Tokens des Kontexts
    truncated: bool = False                          # Budget erreicht?

    @property
    def skipped(self) -> int:
        return self.total_source - len(self.collected)


def collect_source_files(root: Path) -> list[FileEntry]:
    out: list[FileEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _dir_ignored(d)]  # Ballast nicht betreten
        for fn in filenames:
            p = Path(dirpath) / fn
            if not _is_source(p):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size == 0 or size > MAX_FILE_BYTES:
                continue
            out.append(FileEntry(relpath=str(p.relative_to(root)), size=size))
    return out


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def build_context(
    root: str | Path,
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> tuple[str, ScanResult]:
    """Sammelt Quellcode bis zum Token-Budget und baut den Kontext-String.
    Kleine Dateien zuerst → maximale Datei-Abdeckung."""
    root = Path(root).expanduser().resolve()
    files = collect_source_files(root)
    files.sort(key=lambda f: f.size)

    result = ScanResult(root=str(root), total_source=len(files))
    parts: list[str] = []
    for fe in files:
        try:
            text = (root / fe.relpath).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        block = f"### FILE: {fe.relpath}\n{text}\n"
        t = _estimate_tokens(block)
        if result.tokens + t > budget_tokens and result.collected:
            result.truncated = True
            break
        parts.append(block)
        result.tokens += t
        result.collected.append(fe)

    return "\n".join(parts), result
