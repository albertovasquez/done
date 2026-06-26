import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import os
from pathlib import Path
from harness import paths


def test_config_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert paths.config_dir() == tmp_path / "harness"


def test_config_dir_defaults_to_home_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.config_dir() == tmp_path / ".config" / "harness"


def test_config_dir_does_not_create(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    d = paths.config_dir()
    assert not d.exists()


def test_load_env_precedence(monkeypatch, tmp_path):
    # process env wins over project .env wins over config .env; gaps filled only
    proj = tmp_path / "proj"; proj.mkdir()
    cfg = tmp_path / "cfg"; cfg.mkdir()
    (proj / ".env").write_text("A=proj\nB=proj\n")
    (cfg / ".env").write_text("A=cfg\nB=cfg\nC=cfg\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))   # config_dir -> tmp_path/harness
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness" / ".env").write_text("A=cfg\nB=cfg\nC=cfg\n")
    monkeypatch.setenv("A", "env")        # already-set: must win
    monkeypatch.delenv("B", raising=False)
    monkeypatch.delenv("C", raising=False)
    paths.load_env(proj)
    assert os.environ["A"] == "env"       # process env untouched
    assert os.environ["B"] == "proj"      # project .env beats config .env
    assert os.environ["C"] == "cfg"       # only in config .env


def test_load_env_no_files_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    paths.load_env(tmp_path)              # no .env anywhere -> no exception


def test_mini_yaml_path_exists():
    p = paths.mini_yaml_path()
    assert p.name == "mini.yaml"
    assert p.is_file()


def test_load_env_reads_project_env_from_explicit_cwd(monkeypatch, tmp_path):
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".env").write_text("PROJ_ONLY=yes\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nocfg"))  # no config .env
    monkeypatch.delenv("PROJ_ONLY", raising=False)
    paths.load_env(str(proj))
    import os
    assert os.environ["PROJ_ONLY"] == "yes"


def test_skills_dirs_orders_bundled_then_user(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "harness" / "skills").mkdir(parents=True)   # user dir exists
    dirs = paths.skills_dirs()
    assert dirs[0] == paths.bundled_skills_dir()            # bundled first (lowest precedence)
    assert dirs[-1] == tmp_path / "harness" / "skills"      # user last (wins)


def test_default_workspace_dir_under_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert paths.default_workspace_dir() == tmp_path / "harness" / "agents" / "default"


def test_default_workspace_dir_does_not_create(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    d = paths.default_workspace_dir()
    assert not d.exists()
