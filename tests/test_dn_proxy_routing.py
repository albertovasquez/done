import pytest
from harness import tui_main


def test_dn_proxy_routes_to_cli_without_launching_tui(monkeypatch):
    seen = {}
    monkeypatch.setattr("harness.proxy_service.cli.run",
                        lambda argv: (seen.__setitem__("argv", argv) or 0))
    monkeypatch.setattr(tui_main, "HarnessTui",
                        lambda *a, **k: pytest.fail("TUI must not launch for `dn proxy`"))
    rc = tui_main.main(["proxy", "status"])
    assert rc == 0
    assert seen["argv"] == ["status"]


def test_proxy_cli_loads_config_dot_env(monkeypatch, tmp_path):
    """`dn proxy` returns before tui_main's load_env, so proxy_cli.run() must load
    ~/.config/harness/.env itself — otherwise config_gen never sees a key that
    lives only in that file (e.g. NEURALWATT_API_KEY), and glm/qwen silently
    vanish from the generated config."""
    import os
    from harness import paths
    from harness.proxy_service import cli as proxy_cli, lifecycle

    # A .env in the config dir holding a key that is NOT in the process env.
    (tmp_path / ".env").write_text("NEURALWATT_API_KEY=nw-from-dotenv\n")
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("NEURALWATT_API_KEY", raising=False)
    assert "NEURALWATT_API_KEY" not in os.environ      # precondition

    # Stub the actual command so the test doesn't shell out; we only assert the
    # env was loaded by the time dispatch happens.
    seen = {}
    monkeypatch.setattr(lifecycle, "status",
                        lambda: seen.__setitem__("key", os.environ.get("NEURALWATT_API_KEY")) or "ok")
    rc = proxy_cli.run(["status"])
    assert rc == 0
    assert seen["key"] == "nw-from-dotenv"


def test_proxy_cli_does_not_override_exported_key(monkeypatch, tmp_path):
    """Shell-exported key must win over the .env value (load_env override=False).
    Documented precedence: shell env > .env."""
    import os
    from harness import paths
    from harness.proxy_service import cli as proxy_cli, lifecycle

    (tmp_path / ".env").write_text("NEURALWATT_API_KEY=nw-from-dotenv\n")
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("NEURALWATT_API_KEY", "nw-from-shell")

    seen = {}
    monkeypatch.setattr(lifecycle, "status",
                        lambda: seen.__setitem__("key", os.environ.get("NEURALWATT_API_KEY")) or "ok")
    proxy_cli.run(["status"])
    assert seen["key"] == "nw-from-shell"
