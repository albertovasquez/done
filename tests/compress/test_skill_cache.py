from harness.compress import skill_cache, rules


def _redirect(monkeypatch, tmp_path):
    from harness import paths
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)


def test_key_changes_with_source_and_rules(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    k1 = skill_cache.cache_key("body A")
    assert len(k1) == 16
    assert skill_cache.cache_key("body B") != k1          # source changes key
    monkeypatch.setattr(rules, "rules_sha256", lambda: "0" * 64)
    assert skill_cache.cache_key("body A") != k1          # rules bump changes key


def test_store_then_cached_roundtrip(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    src = "verbose original body"
    assert skill_cache.cached_body(src) is None            # miss before store
    skill_cache.store_body(src, "terse body")
    assert skill_cache.cached_body(src) == "terse body"    # hit after store


def test_cached_body_misses_when_source_changes(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    skill_cache.store_body("old body", "terse")
    assert skill_cache.cached_body("new body") is None     # different key -> miss


def test_cached_body_never_raises_on_missing_dir(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path / "does-not-exist")
    assert skill_cache.cached_body("anything") is None
