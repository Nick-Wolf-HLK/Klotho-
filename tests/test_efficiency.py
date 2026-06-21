"""Tests für die Effizienz-/Robustheit-Maßnahmen: 429-Backoff, Vorab-Schätzung,
Judge-Fallback, Voll-Audit-Tasks (ein Agent pro Chunk)."""
import asyncio

import httpx
import pytest

import klotho.llm_client as L
from klotho import coverage, judge
from klotho.plan_schema import SubagentResponse


async def _noop_sleep(_s):
    return None


def _patch_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(L.httpx, "AsyncClient",
                        lambda **kw: real(transport=transport, timeout=kw.get("timeout")))
    monkeypatch.setattr(L.asyncio, "sleep", _noop_sleep)


def test_backoff_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _patch_transport(monkeypatch, handler)
    client = L.LLMClient()
    res = asyncio.run(client.chat("m", [{"role": "user", "content": "hi"}]))
    assert res.text == "ok"
    assert calls["n"] == 3                     # 2× 429, dann Erfolg


def test_backoff_raises_after_exhausting(monkeypatch):
    def handler(request):
        return httpx.Response(429)
    _patch_transport(monkeypatch, handler)
    client = L.LLMClient()
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(client.chat("m", [{"role": "user", "content": "hi"}]))


def test_chat_with_tools_uses_backoff(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": []}}]})
    _patch_transport(monkeypatch, handler)
    client = L.LLMClient()
    msg = asyncio.run(client.chat_with_tools("m", [{"role": "user", "content": "x"}], []))
    assert calls["n"] == 2
    assert msg["tool_calls"] == []


def test_estimate_audit_one_call_per_chunk(tmp_path):
    files = []
    for i in range(20):
        p = tmp_path / f"f{i}.py"
        p.write_text("x = 1\n")
        files.append(f"f{i}.py")
    est = coverage.estimate_audit(files, str(tmp_path), max_files=5, max_chars=10_000, max_rounds=1)
    assert est["files"] == 20
    assert est["chunks"] == 4                    # 20 Dateien / 5 pro Chunk
    assert est["calls_per_round"] == 4           # 1 Call pro Chunk (kein Loop)
    assert est["calls_total"] == 4


def test_estimate_audit_scales_with_rounds(tmp_path):
    files = []
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("x = 1\n")
        files.append(f"f{i}.py")
    est = coverage.estimate_audit(files, str(tmp_path), max_files=5, max_chars=10_000, max_rounds=2)
    assert est["chunks"] == 2
    assert est["calls_total"] == 4               # 2 Chunks × 2 Runden


def test_build_tasks_single_agent_default():
    tasks = coverage.build_tasks([["a.py"], ["b.py"]], ["m1", "m2"])
    assert len(tasks) == 2                       # ein Audit pro Chunk
    assert all(t.lens is None for t in tasks)


def test_equal_weight_report_skips_errored():
    responses = [
        SubagentResponse(agent="a", model="m", response="ok"),
        SubagentResponse(agent="b", model="m", response="ok"),
        SubagentResponse(agent="c", model="m", response="", error="429"),
    ]
    rep = judge.equal_weight_report(responses)
    assert len(rep.verdicts) == 2                # erroneous c rausgefiltert
    assert abs(sum(v.weight for v in rep.verdicts) - 1.0) < 1e-9
    assert rep.best_agent in ("a", "b")
