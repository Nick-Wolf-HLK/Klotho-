"""Tests für die deterministische Befund-Verifikation gegen den Quellcode."""
from klotho import verify
from klotho.plan_schema import Finding


def _write(tmp_path, rel, content):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_confirms_real_quote_and_corrects_line(tmp_path):
    _write(tmp_path, "app.py", "import os\n\ndef run():\n    secret = get_token()\n    return secret\n")
    # Auditor behauptet Zeile 99 — das Zitat steht aber wirklich in Zeile 4.
    f = Finding(file="app.py", line=99, severity="high", category="security",
                issue="Token im Klartext", code_quote="secret = get_token()", fix="…")
    kept, dropped = verify.verify_findings([f], str(tmp_path))
    assert dropped == 0
    assert len(kept) == 1
    assert kept[0].confidence == "confirmed"
    assert kept[0].line == 4          # Zeile korrigiert


def test_drops_hallucinated_quote(tmp_path):
    _write(tmp_path, "app.py", "x = 1\ny = 2\n")
    f = Finding(file="app.py", line=5, code_quote="user.profil  # typo never existed")
    kept, dropped = verify.verify_findings([f], str(tmp_path))
    assert kept == []
    assert dropped == 1


def test_drops_finding_for_missing_file(tmp_path):
    f = Finding(file="does/not/exist.py", line=1, code_quote="whatever()")
    kept, dropped = verify.verify_findings([f], str(tmp_path))
    assert kept == []
    assert dropped == 1


def test_whitespace_insensitive_match(tmp_path):
    _write(tmp_path, "a.py", "def f():\n        return    1\n")
    f = Finding(file="a.py", line=2, code_quote="return 1")   # andere Einrückung/Spacing
    kept, dropped = verify.verify_findings([f], str(tmp_path))
    assert dropped == 0
    assert kept[0].confidence == "confirmed"
    assert kept[0].line == 2


def test_finding_without_quote_kept_as_unconfirmed(tmp_path):
    f = Finding(file="a.py", line=3, code_quote="", issue="vage Vermutung")
    kept, dropped = verify.verify_findings([f], str(tmp_path))
    assert dropped == 0
    assert len(kept) == 1
    assert kept[0].confidence == "unconfirmed"


def test_sandbox_escape_is_dropped(tmp_path):
    f = Finding(file="../../etc/passwd", line=1, code_quote="root:x:0:0")
    kept, dropped = verify.verify_findings([f], str(tmp_path))
    assert kept == []
    assert dropped == 1
