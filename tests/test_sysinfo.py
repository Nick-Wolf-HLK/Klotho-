"""Tests für den lokalen RAM-Check (welche Modelle passen in den Speicher)."""
from klotho import sysinfo

GB = 1_000_000_000


def test_runnable_filters_by_ram():
    sizes = {"small:7b": 4 * GB, "big:70b": 40 * GB}
    ram = 16 * GB                                  # Budget = 0.7 * 16 = 11.2 GB
    runnable, too_big = sysinfo.runnable_local_models(
        ["small:7b", "big:70b"], sizes, ram)
    assert runnable == ["small:7b"]
    assert too_big == ["big:70b"]


def test_runnable_without_ram_allows_all():
    sizes = {"m:7b": 99 * GB}
    runnable, too_big = sysinfo.runnable_local_models(["m:7b"], sizes, 0)
    assert runnable == ["m:7b"]                     # RAM unbekannt → kein Ausschluss
    assert too_big == []


def test_runnable_without_size_allows_model():
    runnable, too_big = sysinfo.runnable_local_models(["unknown:1b"], {}, 8 * GB)
    assert runnable == ["unknown:1b"]              # Größe unbekannt → erlauben
    assert too_big == []


def test_runnable_matches_base_name():
    sizes = {"gemma4": 2 * GB}                      # /api/tags nur Basisname
    runnable, _ = sysinfo.runnable_local_models(["gemma4:12b"], sizes, 16 * GB)
    assert runnable == ["gemma4:12b"]


def test_ollama_root_strips_v1():
    assert sysinfo._ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert sysinfo._ollama_root("http://x:1/v1/") == "http://x:1"
