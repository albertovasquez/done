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


def test_base_url_and_api_key_dual_name(monkeypatch):
    monkeypatch.delenv("VIBEPROXY_BASE_URL", raising=False)
    monkeypatch.setenv("PROXY_BASE_URL", "http://x/v1")
    assert vibeproxy.base_url() == "http://x/v1"
    monkeypatch.delenv("PROXY_BASE_URL", raising=False)
    monkeypatch.setenv("VIBEPROXY_BASE_URL", "http://y/v1")
    assert vibeproxy.base_url() == "http://y/v1"


def _isolate_api_key(monkeypatch, tmp_path):
    from harness.proxy_service import paths as proxy_paths
    monkeypatch.delenv("PROXY_API_KEY", raising=False)
    monkeypatch.delenv("VIBEPROXY_API_KEY", raising=False)
    monkeypatch.setattr(proxy_paths, "data_dir", lambda: tmp_path)
    return proxy_paths


def test_api_key_env_wins_over_client_key_file(monkeypatch, tmp_path):
    proxy_paths = _isolate_api_key(monkeypatch, tmp_path)
    proxy_paths.client_key_path().write_text("file-key")
    monkeypatch.setenv("PROXY_API_KEY", "env-key")
    assert vibeproxy.api_key() == "env-key"


def test_api_key_falls_back_to_client_key_file(monkeypatch, tmp_path):
    proxy_paths = _isolate_api_key(monkeypatch, tmp_path)
    proxy_paths.client_key_path().write_text("file-key\n")
    assert vibeproxy.api_key() == "file-key"


def test_api_key_dummy_when_no_env_and_no_file(monkeypatch, tmp_path):
    _isolate_api_key(monkeypatch, tmp_path)
    assert vibeproxy.api_key() == "dummy-not-used"


def test_api_key_sentinel_env_does_not_mask_client_key_file(monkeypatch, tmp_path):
    # Pre-#300 .env files carry the placeholder; it must be treated as absent,
    # not as an override, or every call 401s once the proxy enforces api-keys.
    proxy_paths = _isolate_api_key(monkeypatch, tmp_path)
    proxy_paths.client_key_path().write_text("file-key")
    monkeypatch.setenv("VIBEPROXY_API_KEY", "dummy-not-used")
    assert vibeproxy.api_key() == "file-key"
    monkeypatch.setenv("PROXY_API_KEY", "dummy-not-used")
    assert vibeproxy.api_key() == "file-key"
