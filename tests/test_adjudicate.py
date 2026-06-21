"""Tests für die adversariale Befund-Gegenprüfung (mit Fake-Client)."""
import asyncio

from klotho import adjudicate
from klotho.plan_schema import Finding


class _FakeClient:
    """Gibt sofort ein finales Urteil zurück (keine Tool-Runden)."""
    def __init__(self, content: str):
        self._content = content

    async def chat_with_tools(self, model, messages, tools):
        return {"role": "assistant", "content": self._content, "tool_calls": []}


def _finding(**kw):
    base = dict(file="a.py", line=1, severity="critical", category="bug",
                issue="x", code_quote="eval(x)", fix="f")
    base.update(kw)
    return Finding(**base)


def _run(client, findings, tmp_path):
    (tmp_path / "a.py").write_text("eval(x)\n")
    return asyncio.run(
        adjudicate.adjudicate_findings(client, "m", findings, str(tmp_path))
    )


def test_refuted_finding_is_dropped(tmp_path):
    client = _FakeClient('{"verdict":"refuted","severity":"low","reason":"field exists"}')
    kept, refuted = _run(client, [_finding()], tmp_path)
    assert kept == []
    assert refuted == 1


def test_confirmed_recalibrates_severity(tmp_path):
    client = _FakeClient('{"verdict":"confirmed","severity":"medium","reason":"real but minor"}')
    kept, refuted = _run(client, [_finding(severity="critical")], tmp_path)
    assert refuted == 0
    assert len(kept) == 1
    assert kept[0].severity == "medium"          # herabgestuft
    assert kept[0].confidence == "confirmed"


def test_uncertain_is_kept_as_unconfirmed(tmp_path):
    client = _FakeClient('{"verdict":"uncertain","severity":"low","reason":"cannot tell"}')
    kept, refuted = _run(client, [_finding()], tmp_path)
    assert refuted == 0
    assert kept[0].confidence == "unconfirmed"


def test_unparseable_verdict_keeps_finding(tmp_path):
    client = _FakeClient("I could not decide, sorry.")
    kept, refuted = _run(client, [_finding()], tmp_path)
    assert refuted == 0
    assert len(kept) == 1                         # im Zweifel behalten


def test_empty_findings_short_circuits(tmp_path):
    client = _FakeClient('{"verdict":"refuted"}')
    kept, refuted = _run(client, [], tmp_path)
    assert kept == [] and refuted == 0
