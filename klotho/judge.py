"""LLM-as-judge: scores subagent responses against a rubric."""
from __future__ import annotations

import json
from typing import Optional

from . import compress
from .compress import CompressionStats
from .config import RubricConfig
from .llm_client import LLMClient
from .plan_schema import (
    AgentVerdict,
    CriterionScore,
    JudgeReport,
    SubagentResponse,
)


JUDGE_SYSTEM = (
    "You are an impartial judge evaluating planning proposals from multiple "
    "AI subagents. Score each response on a 0-10 scale for every criterion. "
    "Then assign a weight (0..1) to each response so that all weights sum to "
    "1.0. Higher weight = more of this response should flow into the final "
    "synthesis. Respond strictly as JSON matching the requested schema."
)


def _build_judge_prompt(
    prompt: str,
    responses: list[SubagentResponse],
    rubric: RubricConfig,
    compression: str = "safe",
    stats: Optional[CompressionStats] = None,
) -> str:
    blocks = []
    for r in responses:
        body = r.response if r.response else f"(ERROR: {r.error})"
        body = compress.compress_text(body, compression, stats=stats)
        blocks.append(
            f"### Agent: {r.agent} (model: {r.model})\n{body}"
        )
    joined = "\n\n".join(blocks)
    criteria = rubric.criteria
    schema = {
        "verdicts": [
            {
                "agent": "<agent name>",
                "model": "<model>",
                "total_score": 0.0,
                "weight": 0.0,
                "criteria": [
                    {"criterion": c, "score": 0.0, "rationale": ""}
                    for c in criteria
                ],
                "notes": "",
            }
        ],
        "best_agent": "<agent with highest total_score>",
        "summary": "one-paragraph comparison",
    }
    return (
        f"ORIGINAL TASK PROMPT:\n{prompt}\n\n"
        f"SUBAGENT RESPONSES:\n{joined}\n\n"
        f"Score each response on these criteria: {criteria}.\n"
        "total_score = mean of criteria scores.\n"
        "Weights must be non-negative and sum to 1.0; allocate more weight "
        "to stronger responses.\n"
        "Respond ONLY as JSON with this schema:\n"
        f"{compress.compact_json(schema)}"
    )


async def judge_responses(
    client: LLMClient,
    judge_model: str,
    prompt: str,
    responses: list[SubagentResponse],
    rubric: RubricConfig,
    *,
    compression: str = "safe",
    stats: Optional[CompressionStats] = None,
) -> JudgeReport:
    user_prompt = _build_judge_prompt(prompt, responses, rubric, compression, stats)
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    data = await client.chat_json(judge_model, messages, temperature=0.1)
    return _coerce_report(data, responses)


def _coerce_report(
    data: dict, responses: list[SubagentResponse]
) -> JudgeReport:
    verdicts: list[AgentVerdict] = []
    by_agent = {r.agent: r for r in responses}
    for v in data.get("verdicts", []):
        agent = v.get("agent", "?")
        model = v.get("model") or by_agent.get(agent, SubagentResponse(agent=agent, model="?", response="")).model
        criteria = [
            CriterionScore(
                criterion=c.get("criterion", "?"),
                score=float(c.get("score", 0.0)),
                rationale=c.get("rationale", ""),
            )
            for c in v.get("criteria", [])
        ]
        verdicts.append(
            AgentVerdict(
                agent=agent,
                model=model,
                total_score=float(v.get("total_score", 0.0)),
                weight=float(v.get("weight", 0.0)),
                criteria=criteria,
                notes=v.get("notes", ""),
            )
        )
    best = data.get("best_agent") or (
        max(verdicts, key=lambda x: x.total_score).agent if verdicts else ""
    )
    return JudgeReport(
        verdicts=verdicts,
        best_agent=best,
        summary=data.get("summary", ""),
    )