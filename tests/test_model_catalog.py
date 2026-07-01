import json
from harness import model_catalog


_FAKE_API = {
    "neuralwatt": {"name": "NeuralWatt", "env": ["NEURALWATT_API_KEY"],
                   "models": {"glm-5.2": {"name": "GLM 5.2"}, "qwen3.5-397b-fast": {"name": "Qwen3.5"}}},
    "anthropic": {"name": "Anthropic", "env": ["ANTHROPIC_API_KEY"],
                  "models": {"claude-opus-4-8": {"name": "Claude Opus 4.8"}}},
    "some-unsupported": {"name": "X", "env": [], "models": {"z": {"name": "Z"}}},
}


def test_parses_supported_providers_from_fetch(tmp_path):
    provs = model_catalog.providers(
        fetch=lambda: json.dumps(_FAKE_API),
        cache_path=tmp_path / "models.json",
        now=lambda: 1000.0,
    )
    by_id = {p.id: p for p in provs}
    assert "neuralwatt" in by_id and "anthropic" in by_id
    assert by_id["neuralwatt"].env == ["NEURALWATT_API_KEY"]
    assert {m.id for m in by_id["neuralwatt"].models} == {"glm-5.2", "qwen3.5-397b-fast"}
    # unsupported providers filtered out
    assert "some-unsupported" not in by_id


def test_falls_back_to_bundled_snapshot_when_fetch_fails(tmp_path):
    def boom():
        raise OSError("network down")
    provs = model_catalog.providers(
        fetch=boom, cache_path=tmp_path / "nonexistent.json", now=lambda: 1000.0)
    # snapshot ships with at least neuralwatt + anthropic
    ids = {p.id for p in provs}
    assert "neuralwatt" in ids and "anthropic" in ids


def test_uses_fresh_cache_without_fetching(tmp_path):
    cache = tmp_path / "models.json"
    cache.write_text(json.dumps(_FAKE_API))
    called = {"n": 0}
    def counting_fetch():
        called["n"] += 1
        return json.dumps({})
    # cache mtime is "now"; TTL not exceeded -> no fetch
    provs = model_catalog.providers(fetch=counting_fetch, cache_path=cache, now=lambda: cache.stat().st_mtime + 10)
    assert called["n"] == 0
    assert {p.id for p in provs} >= {"neuralwatt", "anthropic"}


def test_default_cache_path_uses_harness_config_dir(tmp_path, monkeypatch):
    """Verify default cache_path resolves under harness config dir without network."""
    import harness.paths as hp
    monkeypatch.setattr(hp, "config_dir", lambda: tmp_path)

    provs = model_catalog.providers(
        fetch=lambda: json.dumps(_FAKE_API),
        now=lambda: 0.0,  # force cache miss -> fetch + write
    )
    # cache file should be created at <tmp>/models.json (config_dir() / "models.json")
    assert (tmp_path / "models.json").exists()
    # verify data loaded correctly
    assert any(p.id == "anthropic" for p in provs)
    assert any(p.id == "neuralwatt" for p in provs)
