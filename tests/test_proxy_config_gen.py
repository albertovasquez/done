import os
import stat
from harness import paths as harness_paths
from harness.proxy_service import config_gen, paths


def test_generated_config_is_localhost_and_has_no_secret_key():
    yaml_text = config_gen.generate(port=8317)
    assert "port: 8317" in yaml_text
    assert "127.0.0.1" in yaml_text
    # We deliberately do NOT write remote-management.secret-key (it gets hashed).
    assert "secret-key:" not in yaml_text


def test_management_password_is_persisted_0600(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "secret_path", lambda: tmp_path / "management-password")
    pw1 = config_gen.ensure_management_password()
    pw2 = config_gen.ensure_management_password()       # idempotent: same value
    assert pw1 == pw2 and len(pw1) >= 32
    mode = stat.S_IMODE(os.stat(paths.secret_path()).st_mode)
    assert mode == 0o600


def test_generate_includes_neuralwatt_when_key_set():
    y = config_gen.generate(env={"NEURALWATT_API_KEY": "nw-123"})
    assert "openai-compatibility" in y
    assert "api.neuralwatt.com/v1" in y
    # We bind the real upstream ids directly (no aliases): id == alias.
    assert 'alias: "glm-5.2"' in y
    assert "qwen3.5-397b-fast" in y          # the router model upstream id
    assert "glm-5.2" in y                    # GLM upstream id (confirmed live)


def test_generate_neuralwatt_yaml_is_valid():
    import yaml
    y = config_gen.generate(env={"NEURALWATT_API_KEY": "nw-123"})
    d = yaml.safe_load(y)
    models = d["openai-compatibility"][0]["models"]
    aliases = {m["alias"] for m in models}
    assert aliases == {"glm-5.2", "qwen3.5-397b-fast", "glm-5.2-short-fast"}


def test_generate_omits_neuralwatt_when_key_absent():
    y = config_gen.generate(env={})
    assert "openai-compatibility" not in y


def test_generate_pins_auth_dir(tmp_path, monkeypatch):
    """auth-dir must be set so the foreground login and the background service
    share the same auths/ token directory (otherwise tokens diverge by cwd)."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    y = config_gen.generate(env={})
    assert f'auth-dir: "{tmp_path / "auths"}"' in y


def test_alias_to_upstream_is_identity_after_alias_removal():
    """Aliases were removed: the proxy binds the real upstream id, so this map is
    now an identity (id -> same id). Kept so consumers don't break."""
    m = config_gen.alias_to_upstream()
    assert m["qwen3.5-397b-fast"] == "qwen3.5-397b-fast"
    assert m["glm-5.2"] == "glm-5.2"
    assert m["glm-5.2-short-fast"] == "glm-5.2-short-fast"


def test_config_drift_missing_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_path", lambda: tmp_path / "config.yaml")
    assert config_gen.config_drift(env={}) == "missing"


def test_config_drift_ok_when_file_matches_generate(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    env = {"NEURALWATT_API_KEY": "nw-123"}
    cfg_path.write_text(config_gen.generate(env=env))
    assert config_gen.config_drift(env=env) == "ok"


def test_config_drift_drifted_when_key_changed(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    cfg_path.write_text(config_gen.generate(env={"NEURALWATT_API_KEY": "old-key"}))
    assert config_gen.config_drift(env={"NEURALWATT_API_KEY": "new-key"}) == "drifted"


def test_config_drift_drifted_when_key_removed(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    cfg_path.write_text(config_gen.generate(env={"NEURALWATT_API_KEY": "nw-123"}))
    assert config_gen.config_drift(env={}) == "drifted"


def test_config_drift_default_reads_machine_global_env_file(tmp_path, monkeypatch):
    """config_drift()'s default (no env= arg) must resolve NEURALWATT_API_KEY
    from the machine-global ~/.config/harness/.env FILE, not just whatever is
    already in os.environ. This is the TUI vs `dn proxy` asymmetry from the
    review: the TUI process's os.environ may lack a key that only lives in
    the machine-global .env file (never loaded into this test process), yet
    config_drift() must still see it — because that's what install()/upgrade()
    actually baked into config.yaml."""
    cfg_path = tmp_path / "config.yaml"
    machine_global_dir = tmp_path / "machine-global"
    machine_global_dir.mkdir()
    (machine_global_dir / ".env").write_text("NEURALWATT_API_KEY=machine-global-key\n")
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(harness_paths, "config_dir", lambda: machine_global_dir)
    monkeypatch.delenv("NEURALWATT_API_KEY", raising=False)

    # config.yaml was written using the machine-global key (what install() saw).
    cfg_path.write_text(config_gen.generate(env={"NEURALWATT_API_KEY": "machine-global-key"}))

    # No env= override, no process env for the key: default must still resolve
    # "ok" by reading the machine-global .env file directly, not raw os.environ
    # (which is empty of the key in this test process).
    assert config_gen.config_drift() == "ok"


def test_config_drift_default_process_env_wins_over_machine_global_file(tmp_path, monkeypatch):
    """Process-level env (e.g. a real shell export) must still take precedence
    over the machine-global .env FILE, matching load_env()'s documented
    override=False precedence ("process env always wins")."""
    cfg_path = tmp_path / "config.yaml"
    machine_global_dir = tmp_path / "machine-global"
    machine_global_dir.mkdir()
    (machine_global_dir / ".env").write_text("NEURALWATT_API_KEY=stale-file-key\n")
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(harness_paths, "config_dir", lambda: machine_global_dir)
    monkeypatch.setenv("NEURALWATT_API_KEY", "process-env-key")

    # config.yaml matches the process-env key, NOT the stale file key.
    cfg_path.write_text(config_gen.generate(env={"NEURALWATT_API_KEY": "process-env-key"}))

    assert config_gen.config_drift() == "ok"
