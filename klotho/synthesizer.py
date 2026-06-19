"""Synthesizer: builds a MasterPlan from weighted subagent responses."""
from __future__ import annotations

import json
from typing import Optional

from . import compress
from .compress import CompressionStats
from .llm_client import LLMClient
from .plan_schema import JudgeReport, MasterPlan, SubagentResponse


SYNTH_SYSTEM = (
    "You are the synthesis layer of a multi-agent planning system. You "
    "receive several draft plans from subagents along with weights assigned "
    "by a judge. Produce a single unified MasterPlan as JSON. Each step must "
    "be actionable. Use the weighted contributions: pull more from higher-"
    "weighted responses, but merge and deduplicate. Respond ONLY as JSON."
)


def _build_synth_prompt(
    original_prompt: str,
    responses: list[SubagentResponse],
    report: JudgeReport,
    compression: str = "safe",
    stats: Optional[CompressionStats] = None,
) -> str:
    weight_map = {v.agent: v.weight for v in report.verdicts}
    blocks = []
    for r in responses:
        w = weight_map.get(r.agent, 0.0)
        body = compress.compress_text(r.response, compression, stats=stats)
        blocks.append(
            f"### Agent: {r.agent} (weight={w:.2f})\n{body}"
        )
    joined = "\n\n".join(blocks)
    schema = {
        "title": "short title",
        "summary": "1-2 paragraph summary",
        "rationale": "why these steps, referencing weighted sources",
        "sources": {"<agent>": 0.0},
        "steps": [
            {
                "id": 1,
                "title": "step title",
                "description": "what to do",
                "action": "bash|write_file|read_file|web_fetch|noop",
                "command": "shell command or null",
                "path": "file path or null",
                "depends_on": [],
            }
        ],
    }
    return (
        f"ORIGINAL TASK:\n{original_prompt}\n\n"
        f"WEIGHTED DRAFT PLANS:\n{joined}\n\n"
        "Synthesize ONE MasterPlan. Action values must be one of: "
        "bash, write_file, read_file, web_fetch, noop. For bash steps put the "
        "shell command in 'command'. For write_file put target path in 'path' "
        "and the content inside 'description'. depends_on lists step ids.\n"
        "Respond ONLY as JSON matching this schema:\n"
        f"{compress.compact_json(schema)}"
    )


async def synthesize_plan(
    client: LLMClient,
    synth_model: str,
    original_prompt: str,
    responses: list[SubagentResponse],
    report: JudgeReport,
    *,
    compression: str = "safe",
    stats: Optional[CompressionStats] = None,
) -> MasterPlan:
    user_prompt = _build_synth_prompt(
        original_prompt, responses, report, compression, stats
    )
    messages = [
        {"role": "system", "content": SYNTH_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    data = await client.chat_json(synth_model, messages, temperature=0.3)
    return _coerce_plan(data)


def _coerce_plan(data: dict) -> MasterPlan:
    steps_raw = data.get("steps", [])
    steps = []
    for i, s in enumerate(steps_raw, start=1):
        steps.append(
            {
                "id": int(s.get("id", i)),
                "title": str(s.get("title", f"Step {i}")),
                "description": str(s.get("description", "")),
                "action": str(s.get("action", "noop")),
                "command": s.get("command"),
                "path": s.get("path"),
                "depends_on": list(s.get("depends_on", []) or []),
            }
        )
    plan = MasterPlan(
        title=str(data.get("title", "MasterPlan")),
        summary=str(data.get("summary", "")),
        steps=steps,  # type: ignore[arg-type]
        rationale=str(data.get("rationale", "")),
        sources={k: float(v) for k, v in (data.get("sources") or {}).items()},
    )
    return plan