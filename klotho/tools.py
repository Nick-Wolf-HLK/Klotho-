"""Read-only Code-Werkzeuge für agentische Subagenten.

Sandboxed auf einen Wurzelordner: Subagenten dürfen nur LESEN und SUCHEN,
niemals schreiben oder ausführen. Jeder Pfad wird gegen den Root geprüft.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import codebase

MAX_READ_BYTES = 60_000
MAX_GREP_HITS = 80
MAX_LIST = 300
MAX_FIND = 300


class CodeTools:
    """Read-only Dateizugriff, auf ``root`` beschränkt."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._source_files: list[str] | None = None  # gecachte Dateiliste (lazy)

    def _files(self) -> list[str]:
        if self._source_files is None:
            self._source_files = codebase.collect_source_files(self.root)
        return self._source_files

    def _safe(self, rel: str) -> Path:
        rel = (rel or ".").strip()
        p = Path(rel)
        p = (self.root / p).resolve() if not p.is_absolute() else p.resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"Pfad außerhalb des Projektordners: {rel}")
        return p

    # --- Werkzeuge ---------------------------------------------------------
    def list_dir(self, path: str = ".") -> str:
        d = self._safe(path)
        if not d.is_dir():
            return f"(kein Verzeichnis: {path})"
        out = []
        for name in sorted(os.listdir(d)):
            if codebase._dir_ignored(name):
                continue
            out.append(name + ("/" if (d / name).is_dir() else ""))
        return "\n".join(out[:MAX_LIST]) or "(leer)"

    def read_raw(self, path: str) -> str | None:
        """Unveränderter Dateiinhalt (ohne Zeilennummern), sandboxed. Für die
        Verifikation von Befunden gegen die echte Quelle. None, wenn nicht lesbar."""
        try:
            f = self._safe(path)
        except ValueError:
            return None
        if not f.is_file():
            return None
        try:
            return f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def read_file(self, path: str) -> str:
        f = self._safe(path)
        if not f.is_file():
            return f"(keine Datei: {path})"
        data = f.read_text(encoding="utf-8", errors="replace")
        truncated = len(data) > MAX_READ_BYTES
        data = data[:MAX_READ_BYTES]
        lines = data.splitlines()
        body = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(lines))
        if truncated:
            body += f"\n… (gekürzt bei {MAX_READ_BYTES} Bytes)"
        return body

    def grep(self, pattern: str, path: str = ".") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"(ungültiges Regex: {e})"
        base = self._safe(path)
        roots = [base] if base.is_dir() else [base.parent]
        hits: list[str] = []
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not codebase._dir_ignored(d)]
                for fn in filenames:
                    fp = Path(dirpath) / fn
                    if not codebase._is_source(fp):
                        continue
                    try:
                        with fp.open(encoding="utf-8", errors="replace") as fh:
                            for i, line in enumerate(fh, 1):
                                if rx.search(line):
                                    rel = fp.relative_to(self.root)
                                    hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                                    if len(hits) >= MAX_GREP_HITS:
                                        hits.append(f"… (>{MAX_GREP_HITS} Treffer, abgeschnitten)")
                                        return "\n".join(hits)
                    except OSError:
                        continue
        return "\n".join(hits) if hits else "(keine Treffer)"

    def find_files(self, glob: str = "*") -> str:
        from fnmatch import fnmatch
        out = []
        for rel in self._files():
            if fnmatch(rel, glob) or fnmatch(Path(rel).name, glob):
                out.append(rel)
                if len(out) >= MAX_FIND:
                    break
        return "\n".join(out) if out else "(keine Dateien)"

    # --- Dispatch ----------------------------------------------------------
    def dispatch(self, name: str, args: dict) -> str:
        try:
            if name == "list_dir":
                return self.list_dir(args.get("path", "."))
            if name == "read_file":
                return self.read_file(args["path"])
            if name == "grep":
                return self.grep(args["pattern"], args.get("path", "."))
            if name == "find_files":
                return self.find_files(args.get("glob", "*"))
            return f"(unbekanntes Werkzeug: {name})"
        except Exception as e:  # Sandbox-Verletzung etc. ans Modell zurückmelden
            return f"(Fehler in {name}: {e})"


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "Listet Dateien/Unterordner eines Verzeichnisses (relativ zum Projekt-Root). Ballast wie venv/node_modules wird ausgeblendet.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Verzeichnis, Standard '.'"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Liest eine Quelldatei (relativ zum Projekt-Root) mit Zeilennummern.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Dateipfad"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Sucht ein Regex-Muster rekursiv im Quellcode. Liefert datei:zeile:treffer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex"},
                    "path": {"type": "string", "description": "Start-Verzeichnis, Standard '.'"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Findet Quelldateien per Glob-Muster (z.B. '*.py', '*service*').",
            "parameters": {
                "type": "object",
                "properties": {"glob": {"type": "string", "description": "Glob, Standard '*'"}},
            },
        },
    },
]
