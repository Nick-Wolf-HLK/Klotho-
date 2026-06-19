"""Tests für die TSCG-inspirierte Token-Kompression."""
from klotho import compress
from klotho.compress import CompressionStats, compress_text


def test_safe_strips_trailing_ws_and_collapses_blanks():
    raw = "Zeile 1   \n\n\n\nZeile 2\t\n"
    out = compress_text(raw, "safe")
    assert "   \n" not in out          # kein trailing whitespace
    assert "\n\n\n" not in out          # max. eine Leerzeile
    assert "Zeile 1" in out and "Zeile 2" in out


def test_safe_preserves_code_indentation():
    code = "def f():\n    x = 1\n    return x"
    assert compress_text(code, "safe") == code  # Einrückung unangetastet


def test_off_is_identity():
    raw = "egal   \n\n\n\nwas"
    assert compress_text(raw, "off") == raw


def test_aggressive_truncates_long_text_with_marker():
    long = "\n".join(f"Zeile {i} mit etwas Inhalt hier" for i in range(400))
    out = compress_text(long, "aggressive")
    assert "[TSCG: gekürzt]" in out
    assert len(out) < len(long)


def test_aggressive_keeps_short_text():
    short = "kurz und knackig"
    assert "[TSCG" not in compress_text(short, "aggressive")


def test_stats_track_savings():
    stats = CompressionStats(level="safe")
    compress_text("a   \n\n\n\nb", "safe", stats=stats)
    assert stats.before > 0
    assert stats.after <= stats.before
    assert stats.saved == stats.before - stats.after
    assert 0.0 <= stats.ratio <= 1.0


def test_compact_json_has_no_indent():
    s = compress.compact_json({"a": 1, "b": [1, 2]})
    assert "\n" not in s
    assert ", " not in s  # kompakte Separatoren
