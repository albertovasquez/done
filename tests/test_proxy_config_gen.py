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
