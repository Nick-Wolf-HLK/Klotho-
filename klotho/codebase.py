"""Codebase-Scanner: zählt die echten Quelldateien eines Ordners auf.

Filtert aggressiv Ballast (virtuelle Umgebungen, eingebettete Interpreter,
node_modules, Builds, Caches, Binär-/Lock-Dateien, Riesendateien), damit das
Werkzeug ``find_files`` nur echten Quellcode sieht.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

IGNORE_DIR_PATTERNS = (
    ".git", ".hg", ".svn",
    "venv", "venv-*", "*-venv", ".venv", "env", ".env", "virtualenv",
    "py3*", "python3*", "site-packages",          # eingebettete Interpreter/Libs
    "node_modules", "bower_components", "vendor", "Pods", "__pypackages__",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
    # Build-/Distributions-Artefakte: GLOBS, damit dist-x86, build-arm64 etc. auch
    # erfasst werden (sonst wird eine KOPIE des Quellcodes als eigener Code gescannt).
    "dist", "dist-*", "build", "build-*", "out", "target", "target-*",
    ".next", ".nuxt", ".svelte-kit", ".gradle",
    "_internal", "*.dist-info",          # PyInstaller-onedir / Wheel-Metadaten
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


def _dir_ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in IGNORE_DIR_PATTERNS)


def _is_source(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_EXTS or path.name.lower() in EXTRA_FILENAMES


def collect_source_files(root: str | Path) -> list[str]:
    """Liefert die Pfade aller echten Quelldateien (relativ zu root), Ballast
    gefiltert. Genutzt vom Tool `find_files` und für die Datei-Zählung."""
    root = Path(root).expanduser().resolve()
    out: list[str] = []
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
            out.append(str(p.relative_to(root)))
    return out
