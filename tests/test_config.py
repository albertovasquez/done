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


def test_harness_debug_absent_returns_none(tmp_path):
    assert config.harness_debug() is None          # no file


def test_harness_debug_no_section_returns_none(tmp_path):
    _write(tmp_path, "schema_version = 1\n")
    assert config.harness_debug() is None


def test_harness_debug_true(tmp_path):
    _write(tmp_path, "[harness]\ndebug = true\n")
    assert config.harness_debug() is True


def test_harness_debug_false(tmp_path):
    _write(tmp_path, "[harness]\ndebug = false\n")
    assert config.harness_debug() is False


def test_harness_debug_non_bool_returns_none(tmp_path):
    _write(tmp_path, '[harness]\ndebug = "yes"\n')
    assert config.harness_debug() is None


def test_harness_debug_malformed_returns_none(tmp_path):
    _write(tmp_path, "this is = = not toml [[[")
    assert config.harness_debug() is None


def test_load_corrupt_file_warns(tmp_path, caplog):
    """A done.conf that exists but won't parse silently resets every pin to {} —
    that must be surfaced as a warning, not swallowed."""
    _write(tmp_path, "this is = = not toml [[[")
    with caplog.at_level("WARNING", logger="harness.config"):
        assert config.load() == {}
    assert any("unparseable" in r.message for r in caplog.records), \
        f"corrupt done.conf must warn; got {[r.message for r in caplog.records]}"


def test_load_missing_file_is_quiet(tmp_path, caplog):
    """A missing file is normal first-run state and must NOT warn (only corrupt does)."""
    with caplog.at_level("WARNING", logger="harness.config"):
        assert config.load() == {}
    assert not caplog.records, f"missing file must be quiet; got {[r.message for r in caplog.records]}"


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


def test_round_trip_set_model_then_resolve(tmp_path):
    """ACP persists a model; a later TUI startup resolves it back."""
    from harness import tui_main

    # 1) Persist as the ACP set_model handler would.
    config.save_default(config.AgentConfig(backend="vibeproxy", model="claude-opus-4-8"))

    # 2) A fresh launch with NO --model flag picks it up.
    assert tui_main._resolve_model(None) == ("vibeproxy", "claude-opus-4-8")

    # 3) A launch WITH an explicit flag ignores it.
    assert tui_main._resolve_model("mock") == ("mock", None)


# --- yolo_pinned: read, write (merge), helper ---

def test_load_reads_yolo_pinned_true(tmp_path):
    _write(tmp_path, (
        '[agents.default]\nbackend = "vibeproxy"\nmodel = "gpt-5.4"\n'
        'yolo_pinned = true\n'
    ))
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)


def test_load_yolo_pinned_defaults_false_when_absent(tmp_path):
    _write(tmp_path, '[agents.default]\nbackend = "mock"\nmodel = "x"\n')
    assert config.load_default().yolo_pinned is False


def test_load_yolo_pinned_non_bool_is_false(tmp_path):
    _write(tmp_path, (
        '[agents.default]\nbackend = "mock"\nmodel = "x"\n'
        'yolo_pinned = "nope"\n'      # hand-edit error -> treated as False, not fatal
    ))
    assert config.load_default().yolo_pinned is False


def test_yolo_pinned_helper_false_when_no_config(tmp_path):
    assert config.yolo_pinned() is False


def test_yolo_pinned_helper_reads_default(tmp_path):
    config.update_default(backend="mock", model="x", yolo_pinned=True)
    assert config.yolo_pinned() is True


def test_update_default_pin_preserves_backend_and_model(tmp_path):
    config.save_default(config.AgentConfig(backend="vibeproxy", model="gpt-5.4"))
    config.update_default(yolo_pinned=True)
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)


def test_save_default_preserves_existing_pin(tmp_path):
    # The regression the merge fix exists to prevent: changing the model must
    # NOT clear a pin the user set earlier.
    config.update_default(backend="vibeproxy", model="old", yolo_pinned=True)
    config.save_default(config.AgentConfig(backend="vibeproxy", model="new"))
    got = config.load_default()
    assert got.model == "new"
    assert got.yolo_pinned is True


def test_update_default_unpin_writes_false_and_round_trips(tmp_path):
    config.update_default(backend="mock", model="x", yolo_pinned=True)
    config.update_default(yolo_pinned=False)
    assert config.load_default().yolo_pinned is False


def test_serialize_omits_yolo_pinned_when_false(tmp_path):
    config.save_default(config.AgentConfig(backend="mock", model="x"))
    assert "yolo_pinned" not in config.conf_path().read_text()


def test_serialize_emits_yolo_pinned_true(tmp_path):
    config.update_default(backend="mock", model="x", yolo_pinned=True)
    assert "yolo_pinned = true" in config.conf_path().read_text()


def test_update_default_refuses_incomplete_new_default(tmp_path):
    # /yolo pin before any model was set must NOT write backend=""/model="".
    config.update_default(yolo_pinned=True)
    assert config.load_default() is None        # no incomplete default written
    assert config.yolo_pinned() is False


def test_update_default_creates_default_when_backend_and_model_given(tmp_path):
    # Pinning WITH a backend+model (as the agent now supplies) writes a complete row.
    config.update_default(backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)


def test_update_default_pin_on_existing_default_ok(tmp_path):
    # Updating an already-complete default (just the pin) still works.
    config.save_default(config.AgentConfig(backend="mock", model="x"))
    config.update_default(yolo_pinned=True)
    assert config.load_default().yolo_pinned is True


def test_update_default_preserves_other_agents(tmp_path):
    _write(tmp_path, (
        'schema_version = 1\n'
        '[agents.default]\nbackend = "mock"\nmodel = "old"\n'
        '[agents.6f1c-uuid]\nname = "bill"\nbackend = "vibeproxy"\nmodel = "claude-opus-4-8"\n'
    ))
    config.update_default(yolo_pinned=True)
    agents = config.load()
    assert agents["default"] == config.AgentConfig(backend="mock", model="old", yolo_pinned=True)
    assert agents["6f1c-uuid"] == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8", name="bill")


def test_save_and_load_named_agent(isolated_config):
    config.save_agent("fred", config.AgentConfig(backend="vibeproxy", model="m1"))
    assert config.load_agent("fred") == config.AgentConfig(backend="vibeproxy", model="m1")

def test_named_agent_isolated_from_default(isolated_config):
    config.save_default(config.AgentConfig(backend="vibeproxy", model="d"))
    config.save_agent("fred", config.AgentConfig(backend="vibeproxy", model="f"))
    assert config.load_default() == config.AgentConfig(backend="vibeproxy", model="d")
    assert config.load_agent("fred") == config.AgentConfig(backend="vibeproxy", model="f")

def test_yolo_pinned_per_persona(isolated_config):
    config.update_agent("fred", backend="vibeproxy", model="f", yolo_pinned=True)
    assert config.yolo_pinned("fred") is True
    assert config.yolo_pinned("default") is False

def test_update_agent_refuses_incomplete_create(isolated_config):
    config.update_agent("fred", yolo_pinned=True)   # no backend/model yet
    assert config.load_agent("fred") is None         # nothing written


# --- compress_aware: read, write, helper ---

def test_compress_aware_defaults_on_when_unset(isolated_config):
    # autouse isolated_config already redirects config; no file → default True
    assert config.compress_aware_pinned("default") is True


def test_compress_aware_roundtrip(isolated_config):
    # set False, confirm, then set True, confirm
    config.set_compress_aware("default", False)
    assert config.compress_aware_pinned("default") is False
    config.set_compress_aware("default", True)
    assert config.compress_aware_pinned("default") is True


def test_set_compress_aware_preserves_harness_section(isolated_config):
    conf = config.conf_path()
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text('schema_version = 1\n\n[harness]\ndebug = true\n')
    config.set_compress_aware("default", False)
    txt = conf.read_text()
    assert "[harness]" in txt and "debug = true" in txt
    assert config.compress_aware_pinned("default") is False


def test_set_compress_aware_preserves_existing_agent_tables(isolated_config):
    config.update_agent("default", backend="anthropic", model="claude-x")
    config.set_compress_aware("other", False)   # partial table for a different persona
    agents = config.load()
    assert "default" in agents and agents["default"].model == "claude-x"
