import os
import stat
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
    # both NeuralWatt aliases registered (glm + qwen), and the qwen upstream id
    assert 'alias: "glm"' in y
    assert 'alias: "qwen"' in y
    assert "qwen3.5-397b-fast" in y          # the router model upstream id
    assert "glm-5.2" in y                    # GLM upstream id (confirmed live)


def test_generate_neuralwatt_yaml_is_valid():
    import yaml
    y = config_gen.generate(env={"NEURALWATT_API_KEY": "nw-123"})
    d = yaml.safe_load(y)
    models = d["openai-compatibility"][0]["models"]
    aliases = {m["alias"] for m in models}
    assert aliases == {"glm", "qwen", "glm-fast"}


def test_generate_omits_neuralwatt_when_key_absent():
    y = config_gen.generate(env={})
    assert "openai-compatibility" not in y


def test_generate_pins_auth_dir(tmp_path, monkeypatch):
    """auth-dir must be set so the foreground login and the background service
    share the same auths/ token directory (otherwise tokens diverge by cwd)."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    y = config_gen.generate(env={})
    assert f'auth-dir: "{tmp_path / "auths"}"' in y
