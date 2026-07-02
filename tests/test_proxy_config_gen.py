import os
import stat

import pytest

from harness import paths as harness_paths
from harness.proxy_service import config_gen, paths


@pytest.fixture(autouse=True)
def _isolated_proxy_data_dir(tmp_path, monkeypatch):
    """generate() now reads the machine-global client-api-key file; without
    isolation, a key provisioned on the dev machine would leak into every
    un-monkeypatched generate() call below (the #295 hermeticity lesson).
    Tests that need their own data_dir still override this per-test."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "proxy-data")


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


def test_client_api_key_is_persisted_0600_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    k1 = config_gen.ensure_client_api_key()
    k2 = config_gen.ensure_client_api_key()            # idempotent: same value
    assert k1 == k2 and len(k1) >= 32
    mode = stat.S_IMODE(os.stat(paths.client_key_path()).st_mode)
    assert mode == 0o600


def test_generate_embeds_client_api_key_when_provisioned(tmp_path, monkeypatch):
    import yaml
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    key = config_gen.ensure_client_api_key()
    d = yaml.safe_load(config_gen.generate(env={}))
    assert d["api-keys"] == [key]


def test_generate_keeps_empty_api_keys_before_provisioning():
    # autouse fixture points data_dir at an empty tmp dir: no key file yet.
    assert "api-keys: []" in config_gen.generate(env={})


def test_client_api_key_empty_file_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    paths.client_key_path().write_text("")
    assert config_gen.client_api_key() is None


def test_config_drift_ok_with_client_key_and_drifted_when_key_removed(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    config_gen.ensure_client_api_key()
    cfg_path.write_text(config_gen.generate(env={}))
    assert config_gen.config_drift(env={}) == "ok"
    paths.client_key_path().unlink()                   # key gone → truthful drift
    assert config_gen.config_drift(env={}) == "drifted"


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


def test_machine_global_env_empty_shell_value_does_not_mask_file_key(tmp_path, monkeypatch):
    # Poisoned terminal: shell exports NEURALWATT_API_KEY="" while the real key
    # lives in ~/.config/harness/.env. Empty must be treated as absent.
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=sk-real-key\n")
    monkeypatch.setattr(harness_paths, "config_dir", lambda: cfg_dir)
    monkeypatch.setenv("NEURALWATT_API_KEY", "")
    env = config_gen._machine_global_env()
    assert env.get("NEURALWATT_API_KEY") == "sk-real-key"


def test_machine_global_env_nonempty_shell_value_still_wins(tmp_path, monkeypatch):
    # A REAL shell export keeps #292's documented precedence (process env wins).
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=sk-file-key\n")
    monkeypatch.setattr(harness_paths, "config_dir", lambda: cfg_dir)
    monkeypatch.setenv("NEURALWATT_API_KEY", "sk-shell-key")
    env = config_gen._machine_global_env()
    assert env.get("NEURALWATT_API_KEY") == "sk-shell-key"


def test_machine_global_env_empty_file_value_is_absent(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=\n")
    monkeypatch.setattr(harness_paths, "config_dir", lambda: cfg_dir)
    monkeypatch.delenv("NEURALWATT_API_KEY", raising=False)
    env = config_gen._machine_global_env()
    assert "NEURALWATT_API_KEY" not in env


def test_generate_default_env_is_machine_global(tmp_path, monkeypatch):
    # generate(env=None) must resolve through _machine_global_env(), so
    # install()/upgrade() write keyed configs even from a poisoned shell.
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=sk-real-key\n")
    monkeypatch.setattr(harness_paths, "config_dir", lambda: cfg_dir)
    monkeypatch.setenv("NEURALWATT_API_KEY", "")
    y = config_gen.generate()
    assert "neuralwatt" in y and "sk-real-key" in y


def test_summarize_keyed_config_names_provider_and_model_count():
    from harness.proxy_service import config_gen
    y = config_gen.generate(env={"NEURALWATT_API_KEY": "sk-x"})
    assert config_gen.summarize(y) == "config: neuralwatt (3 models)"


def test_summarize_keyless_config_says_no_providers():
    from harness.proxy_service import config_gen
    y = config_gen.generate(env={})
    s = config_gen.summarize(y)
    assert "NO upstream providers" in s and "NEURALWATT_API_KEY" in s


def test_masking_note_fires_only_on_differing_nonempty_values():
    from harness.proxy_service import config_gen
    assert config_gen.masking_note({"NEURALWATT_API_KEY": "a"}, {"NEURALWATT_API_KEY": "b"})
    assert config_gen.masking_note({"NEURALWATT_API_KEY": "a"}, {"NEURALWATT_API_KEY": "a"}) is None
    assert config_gen.masking_note({}, {"NEURALWATT_API_KEY": "b"}) is None
    assert config_gen.masking_note({"NEURALWATT_API_KEY": "a"}, {}) is None
    assert config_gen.masking_note({"NEURALWATT_API_KEY": "a"}, {"NEURALWATT_API_KEY": ""}) is None


def test_removal_note_names_dropped_provider():
    from harness.proxy_service import config_gen
    old = config_gen.generate(env={"NEURALWATT_API_KEY": "sk-x"})
    new = config_gen.generate(env={})
    assert "neuralwatt" in config_gen.removal_note(old, new)
    assert config_gen.removal_note(new, old) is None      # provider ADDED, not removed
    assert config_gen.removal_note(old, old) is None
