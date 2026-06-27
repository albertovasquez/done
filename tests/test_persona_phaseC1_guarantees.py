import pytest
from harness import config, tui_main, model_resolve, vibeproxy


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    return tmp_path


def test_noop_no_config_resolves_engine_default():
    # no done.conf, no --persona → ladder returns the engine default
    assert model_resolve.resolve_model(
        shell_env=None, dotenv=None, persisted=None,
        engine_default=vibeproxy.DEFAULT_MODEL) == vibeproxy.DEFAULT_MODEL
    # and _resolve_model yields no override (env/default stand)
    assert tui_main._resolve_model(None, None) == ("vibeproxy", None)


def test_backcompat_existing_default_model_preserved():
    # a pre-C1 install: model lives in [agents.default]
    config.save_default(config.AgentConfig(backend="vibeproxy", model="legacy-model"))
    # after C1, a flagless launch still resolves it (model never moved)
    assert tui_main._resolve_model(None, None) == ("vibeproxy", "legacy-model")
    assert tui_main._resolve_model(None, "default") == ("vibeproxy", "legacy-model")
