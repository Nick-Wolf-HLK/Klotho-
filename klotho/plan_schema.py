"""Pydantic schemas for plans, judge verdicts and execution actions."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ActionKind(str, Enum):
    bash = "bash"
    write_file = "write_file"
    read_file = "read_file"
    web_fetch = "web_fetch"
    noop = "noop"


class PlanStep(BaseModel):
    id: int
    title: str
    description: str
    action: ActionKind = ActionKind.noop
    command: Optional[str] = None
    path: Optional[str] = None
    depends_on: list[int] = Field(default_factory=list)


class MasterPlan(BaseModel):
    title: str
    summary: str
    steps: list[PlanStep]
    rationale: str = ""
    sources: dict[str, float] = Field(
        default_factory=dict,
        description="agent_name -> weight (0..1) actually used in synthesis",
    )


class CriterionScore(BaseModel):
    criterion: str
    score: float = Field(ge=0.0, le=10.0)
    rationale: str = ""


class AgentVerdict(BaseModel):
    agent: str
    model: str
    total_score: float = Field(ge=0.0, le=10.0)
    weight: float = Field(ge=0.0, le=1.0)
    criteria: list[CriterionScore] = Field(default_factory=list)
    notes: str = ""


class JudgeReport(BaseModel):
    verdicts: list[AgentVerdict]
    best_agent: str
    summary: str = ""


class Finding(BaseModel):
    """Ein einzelner, gegen den Quellcode prüfbarer Audit-Befund."""
    file: str
    line: int = 0
    severity: str = "low"          # critical | high | medium | low
    category: str = "quality"      # bug | logic | quality | security
    issue: str = ""
    code_quote: str = ""           # WÖRTLICHES Zitat — wird gegen die Quelle geprüft
    fix: str = ""
    confidence: str = "unconfirmed"  # confirmed (Zitat in Quelle gefunden) | unconfirmed


class SubagentResponse(BaseModel):
    agent: str
    model: str
    response: str
    elapsed_ms: int = 0
    error: Optional[str] = None
    findings: list[Finding] = Field(default_factory=list)