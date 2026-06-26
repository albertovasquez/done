import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from pathlib import Path

import pytest

from harness import config


def _write(tmp_path: Path, text: str) -> Path:
    """Point config at an isolated XDG dir and write done.conf into it."""
    cfg = tmp_path / "harness"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "done.conf").write_text(text)
    return cfg


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_conf_path_under_config_dir(tmp_path):
    assert config.conf_path() == tmp_path / "harness" / "done.conf"


def test_load_missing_file_returns_empty(tmp_path):
    assert config.load() == {}


def test_load_empty_file_returns_empty(tmp_path):
    _write(tmp_path, "")
    assert config.load() == {}


def test_load_malformed_toml_returns_empty(tmp_path):
    _write(tmp_path, "this is = = not toml [[[")
    assert config.load() == {}


def test_load_valid_default(tmp_path):
    _write(tmp_path, (
        'schema_version = 1\n'
        '[agents.default]\n'
        'backend = "vibeproxy"\n'
        'model = "gpt-5.4"\n'
    ))
    agents = config.load()
    assert agents["default"] == config.AgentConfig(backend="vibeproxy", model="gpt-5.4")


def test_load_skips_agent_missing_required_fields(tmp_path):
    _write(tmp_path, (
        '[agents.default]\n'
        'backend = "vibeproxy"\n'        # no model -> skipped
        '[agents.other]\n'
        'backend = "mock"\n'
        'model = "x"\n'
    ))
    agents = config.load()
    assert "default" not in agents
    assert agents["other"] == config.AgentConfig(backend="mock", model="x")


def test_load_named_uuid_agent_keeps_name(tmp_path):
    _write(tmp_path, (
        '[agents.6f1c-uuid]\n'
        'name = "bill"\n'
        'backend = "vibeproxy"\n'
        'model = "claude-opus-4-8"\n'
    ))
    agents = config.load()
    assert agents["6f1c-uuid"] == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8", name="bill")


def test_load_default_returns_none_when_absent(tmp_path):
    _write(tmp_path, '[agents.other]\nbackend = "mock"\nmodel = "x"\n')
    assert config.load_default() is None


def test_load_default_returns_entry(tmp_path):
    _write(tmp_path, '[agents.default]\nbackend = "mock"\nmodel = "x"\n')
    assert config.load_default() == config.AgentConfig(backend="mock", model="x")


def test_save_default_round_trips(tmp_path):
    config.save_default(config.AgentConfig(backend="vibeproxy", model="gpt-5.4"))
    assert config.load_default() == config.AgentConfig(backend="vibeproxy", model="gpt-5.4")


def test_save_default_writes_schema_version(tmp_path):
    config.save_default(config.AgentConfig(backend="mock", model="x"))
    text = config.conf_path().read_text()
    assert "schema_version = 1" in text


def test_save_default_creates_config_dir(tmp_path):
    # XDG dir exists (tmp_path) but the harness/ subdir does not yet.
    assert not config.conf_path().parent.exists()
    config.save_default(config.AgentConfig(backend="mock", model="x"))
    assert config.conf_path().is_file()


def test_save_default_preserves_other_agents(tmp_path):
    _write(tmp_path, (
        'schema_version = 1\n'
        '[agents.default]\n'
        'backend = "mock"\n'
        'model = "old"\n'
        '[agents.6f1c-uuid]\n'
        'name = "bill"\n'
        'backend = "vibeproxy"\n'
        'model = "claude-opus-4-8"\n'
    ))
    config.save_default(config.AgentConfig(backend="vibeproxy", model="gpt-5.4"))
    agents = config.load()
    assert agents["default"] == config.AgentConfig(backend="vibeproxy", model="gpt-5.4")
    assert agents["6f1c-uuid"] == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8", name="bill")


def test_save_default_escapes_special_chars(tmp_path):
    tricky = 'weird"model\\name'
    config.save_default(config.AgentConfig(backend="vibeproxy", model=tricky))
    assert config.load_default() == config.AgentConfig(backend="vibeproxy", model=tricky)


def test_save_default_no_partial_file_on_replace(tmp_path):
    # Two sequential saves; the file is always valid and reflects the latest.
    config.save_default(config.AgentConfig(backend="mock", model="a"))
    config.save_default(config.AgentConfig(backend="vibeproxy", model="b"))
    assert config.load_default() == config.AgentConfig(backend="vibeproxy", model="b")
