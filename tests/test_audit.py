"""Tests für den Audit-Prompt-Bau (Bug-Report-Synthese)."""
from klotho import audit
from klotho.plan_schema import AgentVerdict, JudgeReport, SubagentResponse


def _report():
    return JudgeReport(
        verdicts=[
            AgentVerdict(agent="a", model="m", total_score=8.0, weight=0.70, criteria=[]),
            AgentVerdict(agent="b", model="m", total_score=4.0, weight=0.30, criteria=[]),
        ],
        best_agent="a",
        summary="",
    )


def test_prompt_includes_findings_and_weights():
    responses = [SubagentResponse(agent="a", model="m", response="Bug in x.py:1")]
    p = audit._build_audit_prompt("Find bugs", responses, _report())
    assert "Bug in x.py:1" in p
    assert "weight=0.70" in p
    assert "Bug-Report" in p  # Format-Vorgabe vorhanden


def test_prompt_skips_errored_and_empty_responses():
    responses = [
        SubagentResponse(agent="a", model="m", response="echter Befund x.py:2"),
        SubagentResponse(agent="b", model="m", response="", error="timeout"),
        SubagentResponse(agent="c", model="m", response="   "),  # leer
    ]
    p = audit._build_audit_prompt("Find bugs", responses, _report())
    assert "echter Befund" in p
    assert "Auditor: b" not in p
    assert "Auditor: c" not in p
