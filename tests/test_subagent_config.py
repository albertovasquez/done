import harness.subagent_config as sc


def _write_conf(tmp_path, text):
    (tmp_path / "done.conf").write_text(text)


def test_global_unset_falls_back_to_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "conf_path", lambda: tmp_path / "done.conf")
    _write_conf(tmp_path, "schema_version = 1\n")
    assert sc.resolve_subagent_model("default", parent_model="gpt-5.4") == "gpt-5.4"


def test_per_task_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "conf_path", lambda: tmp_path / "done.conf")
    _write_conf(tmp_path, '[subagent]\nmodel = "cheap-global"\n')
    assert sc.resolve_subagent_model(
        "default", per_task="cheap-task", parent_model="gpt-5.4") == "cheap-task"


def test_per_persona_over_global(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "conf_path", lambda: tmp_path / "done.conf")
    _write_conf(tmp_path,
        '[subagent]\nmodel = "cheap-global"\n'
        '[agents.alice]\nbackend = "vibeproxy"\nmodel = "x"\nsubagent_model = "cheap-alice"\n')
    assert sc.resolve_subagent_model("alice", parent_model="gpt-5.4") == "cheap-alice"
    # default persona has no per-persona key => global wins
    assert sc.resolve_subagent_model("default", parent_model="gpt-5.4") == "cheap-global"


def test_max_concurrent_default_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "conf_path", lambda: tmp_path / "done.conf")
    _write_conf(tmp_path, "schema_version = 1\n")
    assert sc.subagent_max_concurrent() == 4
    _write_conf(tmp_path, "[subagent]\nmax_concurrent = 8\n")
    assert sc.subagent_max_concurrent() == 8
