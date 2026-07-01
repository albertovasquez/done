from harness.role_model import resolve_role_candidates


def test_persona_primary_then_fallbacks_then_parent():
    parsed = {"agents": {"bob": {
        "roles": {"reviewer": "R1", "fallback": {"reviewer": ["R2"]}},
    }}}
    got = resolve_role_candidates("bob", "reviewer", parsed, parent_model="P")
    assert got == ["R1", "R2", "P"]


def test_default_role_seeds_when_persona_absent():
    parsed = {"agents": {"default": {"roles": {"reviewer": "DR"}}}}
    got = resolve_role_candidates("alice", "reviewer", parsed, parent_model="P")
    assert got == ["DR", "P"]


def test_persona_over_default_over_parent_order():
    parsed = {"agents": {
        "alice": {"roles": {"worker": "AW", "fallback": {"worker": ["AF"]}}},
        "default": {"roles": {"worker": "DW", "fallback": {"worker": ["DF"]}}},
    }}
    got = resolve_role_candidates("alice", "worker", parsed, parent_model="P")
    assert got == ["AW", "AF", "DW", "DF", "P"]


def test_worker_includes_legacy_subagent_rungs():
    parsed = {"agents": {"alice": {"subagent_model": "LEGACY"}},
              "subagent": {"model": "GLOBAL"}}
    got = resolve_role_candidates("alice", "worker", parsed, parent_model="P")
    assert got == ["LEGACY", "GLOBAL", "P"]


def test_non_worker_role_ignores_legacy_rungs():
    parsed = {"agents": {"alice": {"subagent_model": "LEGACY"}},
              "subagent": {"model": "GLOBAL"}}
    got = resolve_role_candidates("alice", "reviewer", parsed, parent_model="P")
    assert got == ["P"]


def test_malformed_tables_are_skipped_not_raised():
    parsed = {"agents": {"alice": {
        "roles": {"worker": "", "fallback": {"worker": "notalist"}},
    }}}
    got = resolve_role_candidates("alice", "worker", parsed, parent_model="P")
    assert got == ["P"]


def test_dedup_order_preserving():
    parsed = {"agents": {"alice": {"roles": {"worker": "P", "fallback": {"worker": ["P", "X"]}}}}}
    got = resolve_role_candidates("alice", "worker", parsed, parent_model="P")
    assert got == ["P", "X"]


def test_empty_config_is_just_parent():
    assert resolve_role_candidates("nobody", "worker", {}, parent_model="P") == ["P"]


import pytest
from harness import subagent_config


@pytest.fixture
def _cfgdir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path


def _write_conf(tmp_path, text):
    (tmp_path / "done.conf").write_text(text)


def test_subagent_wrapper_prefers_per_task(_cfgdir):
    assert subagent_config.resolve_subagent_model(
        "a", per_task="PT", parent_model="P") == "PT"


def test_subagent_wrapper_reads_legacy_persona_key(_cfgdir):
    _write_conf(_cfgdir, 'schema_version = 1\n[agents.alice]\nsubagent_model = "LEG"\n')
    assert subagent_config.resolve_subagent_model(
        "alice", parent_model="P") == "LEG"


def test_subagent_wrapper_reads_global_subagent_model(_cfgdir):
    _write_conf(_cfgdir, 'schema_version = 1\n[subagent]\nmodel = "GLOB"\n')
    assert subagent_config.resolve_subagent_model(
        "alice", parent_model="P") == "GLOB"


def test_subagent_wrapper_falls_to_parent(_cfgdir):
    _write_conf(_cfgdir, 'schema_version = 1\n')
    assert subagent_config.resolve_subagent_model(
        "alice", parent_model="P") == "P"
