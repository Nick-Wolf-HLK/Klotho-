"""Tests for judge.coerce_report and synthesizer.coerce_plan (no live LLM)."""
from klotho.judge import _coerce_report
from klotho.plan_schema import SubagentResponse
from klotho.synthesizer import _coerce_plan


def test_coerce_report_basic():
    responses = [
        SubagentResponse(agent="minimax", model="minimax-m2.7:cloud", response="x"),
        SubagentResponse(agent="kimi", model="kimi-2.7:cloud", response="y"),
    ]
    data = {
        "verdicts": [
            {
                "agent": "minimax",
                "model": "minimax-m2.7:cloud",
                "total_score": 8.0,
                "weight": 0.6,
                "criteria": [
                    {"criterion": "completeness", "score": 8.0, "rationale": "ok"},
                    {"criterion": "feasibility", "score": 8.0, "rationale": ""},
                ],
                "notes": "strong",
            },
            {
                "agent": "kimi",
                "total_score": 6.0,
                "weight": 0.4,
                "criteria": [
                    {"criterion": "completeness", "score": 6.0, "rationale": ""},
                    {"criterion": "feasibility", "score": 6.0, "rationale": ""},
                ],
            },
        ],
        "best_agent": "minimax",
        "summary": "minimax stronger",
    }
    report = _coerce_report(data, responses)
    assert report.best_agent == "minimax"
    assert len(report.verdicts) == 2
    assert report.verdicts[0].weight == 0.6
    assert report.verdicts[1].model == "kimi-2.7:cloud"
    assert report.verdicts[0].criteria[0].criterion == "completeness"


def test_coerce_plan_basic():
    data = {
        "title": "Test Plan",
        "summary": "do the thing",
        "rationale": "because",
        "sources": {"minimax": 0.6, "kimi": 0.4},
        "steps": [
            {
                "id": 1,
                "title": "Step one",
                "description": "echo hi",
                "action": "bash",
                "command": "echo hi",
                "path": None,
                "depends_on": [],
            },
            {
                "id": 2,
                "title": "Write file",
                "description": "hello world",
                "action": "write_file",
                "path": "out.txt",
                "depends_on": [1],
            },
        ],
    }
    plan = _coerce_plan(data)
    assert plan.title == "Test Plan"
    assert len(plan.steps) == 2
    assert plan.steps[1].action.value == "write_file"
    assert plan.steps[1].depends_on == [1]
    assert plan.sources["minimax"] == 0.6