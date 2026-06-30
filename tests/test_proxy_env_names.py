from harness import vibeproxy


def test_model_set_in_detects_either_name():
    assert vibeproxy.model_set_in({"PROXY_MODEL": "x"}) is True
    assert vibeproxy.model_set_in({"VIBEPROXY_MODEL": "x"}) is True
    assert vibeproxy.model_set_in({"OTHER": "x"}) is False


def test_model_value_prefers_proxy_over_vibeproxy():
    assert vibeproxy.model_value({"PROXY_MODEL": "new", "VIBEPROXY_MODEL": "old"}) == "new"
    assert vibeproxy.model_value({"VIBEPROXY_MODEL": "old"}) == "old"
    assert vibeproxy.model_value({}) is None


def test_model_value_treats_empty_as_absent():
    assert vibeproxy.model_value({"PROXY_MODEL": "", "VIBEPROXY_MODEL": "old"}) == "old"


def test_default_model_reads_proxy_first(monkeypatch):
    monkeypatch.setenv("PROXY_MODEL", "p")
    monkeypatch.setenv("VIBEPROXY_MODEL", "v")
    assert vibeproxy.default_model() == "p"


def test_compress_cli_honors_proxy_model(monkeypatch):
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    monkeypatch.delenv("COMPRESS_MODEL", raising=False)
    monkeypatch.setenv("PROXY_MODEL", "haiku-x")
    from harness import compress_cli
    # Patch out the config reads so the function reaches the env-var fallback.
    monkeypatch.setattr("harness.config.harness_setting", lambda *a, **kw: None)
    monkeypatch.setattr("harness.config.load", lambda: {})
    # _compress_model_name falls back to the worker model env when COMPRESS_MODEL
    # is unset; assert PROXY_MODEL is now seen.
    assert compress_cli._compress_model_name() == "haiku-x"


def test_base_url_and_api_key_dual_name(monkeypatch):
    monkeypatch.delenv("VIBEPROXY_BASE_URL", raising=False)
    monkeypatch.setenv("PROXY_BASE_URL", "http://x/v1")
    assert vibeproxy.base_url() == "http://x/v1"
    monkeypatch.delenv("PROXY_BASE_URL", raising=False)
    monkeypatch.setenv("VIBEPROXY_BASE_URL", "http://y/v1")
    assert vibeproxy.base_url() == "http://y/v1"
