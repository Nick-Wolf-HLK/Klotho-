"""Executor: runs a MasterPlan fully automatically (within cwd lock)."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .plan_schema import MasterPlan, PlanStep


class ExecutionError(RuntimeError):
    pass


class Executor:
    def __init__(
        self,
        root_lock: str = ".",
        log_file: str = "~/.klotho/log.jsonl",
        dry_run: bool = False,
    ) -> None:
        self.root = Path(root_lock).resolve()
        self.log_path = Path(log_file).expanduser()
        self.dry_run = dry_run
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def execute(self, plan: MasterPlan) -> list[dict]:
        results: list[dict] = []
        done: set[int] = set()
        steps_by_id = {s.id: s for s in plan.steps}
        pending = list(plan.steps)
        while pending:
            progressed = False
            for step in list(pending):
                if not all(d in done for d in step.depends_on):
                    continue
                result = self._run_step(step)
                results.append(result)
                done.add(step.id)
                pending.remove(step)
                progressed = True
                if not result["ok"] and self._is_blocking(step):
                    raise ExecutionError(
                        f"Step {step.id} failed: {result.get('error')}"
                    )
            if not progressed:
                unresolved = [s.id for s in pending]
                raise ExecutionError(
                    f"Unresolvable step dependencies: {unresolved}"
                )
        return results

    def _is_blocking(self, step: PlanStep) -> bool:
        # bash and write_file are blocking; reads/web/noop are advisory
        return step.action.value in ("bash", "write_file")

    def _run_step(self, step: PlanStep) -> dict:
        record = {
            "step_id": step.id,
            "title": step.title,
            "action": step.action.value,
            "ts": datetime.now(timezone.utc).isoformat(),
            "ok": False,
        }
        try:
            if self.dry_run:
                record["dry_run"] = True
                record["preview"] = self._preview(step)
                record["ok"] = True
                self._log(record)
                return record
            if step.action.value == "bash":
                out = self._run_bash(step.command or "")
                record.update(out)
                record["ok"] = True
            elif step.action.value == "write_file":
                self._write_file(step.path or "", step.description)
                record["path"] = step.path
                record["ok"] = True
            elif step.action.value == "read_file":
                content = self._safe_read(step.path or "")
                record["content_preview"] = content[:500]
                record["ok"] = True
            elif step.action.value == "web_fetch":
                record["url"] = step.command
                record["ok"] = True
            else:
                record["ok"] = True
        except Exception as exc:
            record["error"] = str(exc)
        self._log(record)
        return record

    def _run_bash(self, command: str) -> dict:
        if not command.strip():
            raise ExecutionError("empty bash command")
        proc = subprocess.run(
            command,
            shell=True,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "command": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
        }

    def _safe_path(self, target: str) -> Path:
        p = Path(target).expanduser()
        if not p.is_absolute():
            p = self.root / p
        resolved = p.resolve()
        if self.root not in resolved.parents and resolved != self.root:
            raise ExecutionError(
                f"path {resolved} is outside root lock {self.root}"
            )
        return resolved

    def _write_file(self, path: str, content: str) -> None:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def _safe_read(self, path: str) -> str:
        target = self._safe_path(path)
        return target.read_text()

    def _preview(self, step: PlanStep) -> str:
        if step.action.value == "bash":
            return f"WOULD RUN: {step.command}"
        if step.action.value == "write_file":
            return f"WOULD WRITE {step.path} ({len(step.description)} chars)"
        if step.action.value == "read_file":
            return f"WOULD READ {step.path}"
        return step.action.value

    def _log(self, record: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")