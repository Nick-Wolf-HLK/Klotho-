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


def test_estimate_audit_single_agent_per_chunk():
    est = coverage.estimate_audit(293, chunk_size=10, max_rounds=1)
    assert est["chunks"] == 30
    assert est["agents_per_round"] == 30        # EIN Agent pro Chunk (keine Lens-Explosion)
    assert est["agents_total"] == 30


def test_estimate_audit_multi_lens_is_more():
    est = coverage.estimate_audit(100, chunk_size=10, max_rounds=2, lenses=coverage.LENSES)
    assert est["chunks"] == 10
    assert est["agents_per_round"] == 60        # 10 × 6 Lenses
    assert est["agents_total"] == 120           # × 2 Runden


def test_build_tasks_single_agent_default():
    tasks = coverage.build_tasks([["a.py"], ["b.py"]], ["m1", "m2"])
    assert len(tasks) == 2                       # ein Voll-Audit-Agent pro Chunk
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
