"""Tests für den Audit-Prompt-Bau (Bug-Report-Synthese)."""
from klotho import audit
from klotho.plan_schema import AgentVerdict, Finding, JudgeReport, SubagentResponse


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


def test_build_bug_report_empty_without_structured_findings():
    # Keine strukturierten Findings → leerer String (Caller nutzt LLM-Fallback).
    responses = [SubagentResponse(agent="a", model="m", response="nur Prosa")]
    assert audit.build_bug_report(responses, _report(), "/tmp") == ""


def test_build_bug_report_drops_hallucinated_and_keeps_real(tmp_path):
    (tmp_path / "x.py").write_text("def f():\n    eval(user_input)\n    return 1\n")
    responses = [
        SubagentResponse(agent="a", model="m", response="…", findings=[
            Finding(file="x.py", line=2, severity="critical", category="security",
                    issue="eval auf Nutzereingabe", code_quote="eval(user_input)", fix="ast.literal_eval"),
            Finding(file="x.py", line=9, severity="high", category="bug",
                    issue="erfunden", code_quote="os.system(rm_rf)  # nicht vorhanden"),
        ]),
    ]
    md = audit.build_bug_report(responses, _report(), str(tmp_path))
    assert "eval auf Nutzereingabe" in md
    assert "erfunden" not in md          # halluziniert → verworfen
    assert "1 unbelegte" in md or "1 unbacked" in md
    assert "x.py:2" in md


def test_build_bug_report_consensus_dedup(tmp_path):
    (tmp_path / "y.py").write_text("a = 1\npassword = 'hunter2'\n")
    quote = "password = 'hunter2'"
    responses = [
        SubagentResponse(agent="a", model="m", response="…", findings=[
            Finding(file="y.py", line=2, severity="medium", category="security",
                    issue="hardcoded password", code_quote=quote)]),
        SubagentResponse(agent="b", model="m", response="…", findings=[
            Finding(file="y.py", line=2, severity="high", category="security",
                    issue="secret im Code", code_quote=quote)]),
    ]
    findings, per_auditor, dropped = audit._merge_findings(responses, _report(), str(tmp_path))
    assert len(findings) == 1            # gleicher (Datei, Zeile) → dedupliziert
    assert findings[0].severity == "high"  # höchster Schweregrad gewinnt
    assert findings[0].confidence == "confirmed"
    assert per_auditor == {"a": 1, "b": 1}
