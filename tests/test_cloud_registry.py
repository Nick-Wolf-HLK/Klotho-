"""Tests für den Cloud-Modell-Cache (ohne Netzwerk)."""
import json
import time

from klotho import cloud_registry


def test_cached_returns_fresh(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    monkeypatch.setattr(cloud_registry, "CACHE_PATH", cache)
    cache.write_text(json.dumps({"ts": int(time.time()), "models": ["glm-5.2:cloud"]}))
    assert cloud_registry.cached_cloud_models() == ["glm-5.2:cloud"]
    assert cloud_registry.cache_is_stale() is False


def test_cached_ignores_stale(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    monkeypatch.setattr(cloud_registry, "CACHE_PATH", cache)
    cache.write_text(json.dumps({"ts": 0, "models": ["x:cloud"]}))  # uralt
    assert cloud_registry.cached_cloud_models() == []
    assert cloud_registry.cache_is_stale() is True


def test_cached_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_registry, "CACHE_PATH", tmp_path / "nope.json")
    assert cloud_registry.cached_cloud_models() == []


def test_cached_handles_corrupt(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    monkeypatch.setattr(cloud_registry, "CACHE_PATH", cache)
    cache.write_text("{ not json")
    assert cloud_registry.cached_cloud_models() == []
