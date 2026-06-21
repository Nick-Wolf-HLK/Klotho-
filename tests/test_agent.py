"""Tests für das Kontext-Management des agentischen Loops (managed memory)."""
from klotho import agent


def _build_history(n_cycles: int) -> list[dict]:
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(n_cycles):
        msgs.append({"role": "assistant", "content": f"note {i}",
                     "tool_calls": [{"id": str(i)}]})
        msgs.append({"role": "tool", "tool_call_id": str(i),
                     "content": f"RAW CONTENT {i} " * 100})
    return msgs


def test_evict_keeps_only_recent_tool_results():
    msgs = _build_history(10)
    agent._evict_old_tool_results(msgs)
    tools = [m for m in msgs if m["role"] == "tool"]
    kept = [m for m in tools if m["content"] != agent._EVICTED]
    evicted = [m for m in tools if m["content"] == agent._EVICTED]
    assert len(kept) == agent.KEEP_RAW_RESULTS
    assert len(evicted) == 10 - agent.KEEP_RAW_RESULTS


def test_evict_preserves_all_assistant_notes():
    msgs = _build_history(10)
    agent._evict_old_tool_results(msgs)
    notes = [m for m in msgs if m["role"] == "assistant"]
    assert len(notes) == 10
    assert all(m["content"].startswith("note") for m in notes)  # Notizen unberührt


def test_evict_is_idempotent():
    msgs = _build_history(10)
    agent._evict_old_tool_results(msgs)
    agent._evict_old_tool_results(msgs)
    tools = [m for m in msgs if m["role"] == "tool"]
    assert len([m for m in tools if m["content"] == agent._EVICTED]) == 10 - agent.KEEP_RAW_RESULTS


def test_evict_noop_when_few_results():
    msgs = _build_history(agent.KEEP_RAW_RESULTS - 1)
    agent._evict_old_tool_results(msgs)
    tools = [m for m in msgs if m["role"] == "tool"]
    assert not any(m["content"] == agent._EVICTED for m in tools)  # nichts verworfen


def test_parse_findings_clean_json():
    raw = '{"findings": [{"file": "a.py", "line": 12, "severity": "HIGH", ' \
          '"category": "bug", "issue": "x", "code_quote": "foo()", "fix": "y"}]}'
    findings = agent.parse_findings(raw)
    assert findings is not None and len(findings) == 1
    f = findings[0]
    assert f.file == "a.py" and f.line == 12
    assert f.severity == "high"          # normalisiert
    assert f.confidence == "unconfirmed"  # erst Verifikation bestätigt


def test_parse_findings_tolerates_fences_and_prose():
    raw = "Here is my report:\n```json\n{\"findings\": [" \
          "{\"file\": \"b.py\", \"line\": 1, \"code_quote\": \"q\"}]}\n```\nDone."
    findings = agent.parse_findings(raw)
    assert findings is not None and findings[0].file == "b.py"


def test_parse_findings_returns_none_on_garbage():
    assert agent.parse_findings("just some prose, no json at all") is None


def test_parse_findings_clamps_bad_enum_values():
    raw = '{"findings": [{"file": "a.py", "severity": "showstopper", "category": "weird", "code_quote": "z"}]}'
    f = agent.parse_findings(raw)[0]
    assert f.severity == "low"           # unbekannt → konservativ
    assert f.category == "quality"


def test_coverage_directive_suppresses_find_files_when_assigned():
    out = agent._coverage_directive("audit", lens=None, assigned_files=["a.py", "b.py"])
    assert "a.py" in out and "b.py" in out
    assert "find_files" in out and "Do NOT call find_files" in out  # Verschwendung unterbinden


def test_coverage_directive_plain_without_assignment():
    out = agent._coverage_directive("audit", lens=None, assigned_files=None)
    assert out == "audit"               # ohne Zuweisung unverändert
