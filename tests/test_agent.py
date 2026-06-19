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
