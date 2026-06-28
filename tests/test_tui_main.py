import os
import sys
from types import SimpleNamespace as NS


import pytest
from harness import tui_main
from harness.tui_main import _relaunch_args, _relaunch_command


def test_relaunch_args_vibeproxy_no_yolo():
    args = NS(model="vibeproxy", yolo=False)
    assert _relaunch_args(args, "/proj") == ["--model", "vibeproxy", "--cwd", "/proj"]


def test_relaunch_args_mock_with_yolo():
    args = NS(model="mock", yolo=True)
    assert _relaunch_args(args, "/p") == ["--model", "mock", "--cwd", "/p", "--yolo"]


def test_relaunch_command_prefers_executable_launcher(monkeypatch, tmp_path):
    # sys.argv[0] is an executable file (the `dn` console script) → used as argv[0]
    launcher = tmp_path / "dn"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    args = NS(model="mock", yolo=False)
    cmd = _relaunch_command(args, "/p")
    assert cmd == [str(launcher), "--model", "mock", "--cwd", "/p"]


def test_relaunch_command_falls_back_to_dash_m(monkeypatch):
    # sys.argv[0] is not an executable file (e.g. "-c" / a module path) → fallback
    monkeypatch.setattr(sys, "argv", ["not-a-real-file"])
    args = NS(model="vibeproxy", yolo=True)
    cmd = _relaunch_command(args, "/p")
    assert cmd == [sys.executable, "-m", "harness.tui_main",
                   "--model", "vibeproxy", "--cwd", "/p", "--yolo"]


class _FakeApp:
    def __init__(self, reexec):
        self._reexec = reexec
        self.ran = False
    def run(self):
        self.ran = True


def _patch_common(monkeypatch, reexec, config_home):
    """Patch HarnessTui to a fake whose _reexec is controllable, and stub the
    env/path side effects so main() can run headless. Isolates the config dir
    (XDG_CONFIG_HOME) so _resolve_yolo/_resolve_model read an EMPTY done.conf —
    otherwise a real ~/.config/harness/done.conf (e.g. a yolo pin) leaks in and
    appends --yolo to the re-exec argv, breaking the argv assertions."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    app = _FakeApp(reexec)
    monkeypatch.setattr(tui_main, "HarnessTui", lambda **kw: app)
    monkeypatch.setattr(tui_main.paths, "load_env", lambda cwd: None)
    return app


def test_main_reexecs_when_flag_set(monkeypatch, tmp_path):
    app = _patch_common(monkeypatch, reexec=True, config_home=tmp_path / "cfg")
    calls = {}
    def fake_execv(path, argv):
        calls["path"] = path
        calls["argv"] = argv
        raise SystemExit(0)        # execv never returns; simulate by bailing out
    monkeypatch.setattr(tui_main.os, "execv", fake_execv)
    with pytest.raises(SystemExit):
        tui_main.main(["--model", "mock", "--cwd", str(tmp_path)])
    assert app.ran is True
    assert calls["argv"][-4:] == ["--model", "mock", "--cwd", str(tmp_path)]
    assert calls["path"] == calls["argv"][0]


def test_main_no_reexec_when_flag_unset(monkeypatch, tmp_path):
    app = _patch_common(monkeypatch, reexec=False, config_home=tmp_path / "cfg")
    called = {"execv": False}
    monkeypatch.setattr(tui_main.os, "execv",
                        lambda *a: called.__setitem__("execv", True))
    tui_main.main(["--model", "mock", "--cwd", str(tmp_path)])
    assert app.ran is True
    assert called["execv"] is False, "no re-exec when _reexec is False"


def test_main_reexec_oserror_exits_nonzero(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, reexec=True, config_home=tmp_path / "cfg")
    def boom(path, argv):
        raise OSError("no such file")
    monkeypatch.setattr(tui_main.os, "execv", boom)
    with pytest.raises(SystemExit) as ei:
        tui_main.main(["--model", "mock", "--cwd", str(tmp_path)])
    assert ei.value.code == 1
    assert "reload failed to re-exec" in capsys.readouterr().err


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _write_default(xdg, backend, model):
    cfg = xdg / "harness"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "done.conf").write_text(
        f'[agents.default]\nbackend = "{backend}"\nmodel = "{model}"\n'
    )


def test_resolve_explicit_flag_wins_over_config(isolated_config):
    from harness import config  # noqa: F401  (ensures import even if not at top)
    _write_default(isolated_config, "mock", "from-config")
    # User typed --model vibeproxy -> config is ignored, no model override.
    assert tui_main._resolve_model("vibeproxy") == ("vibeproxy", None)


def test_resolve_uses_config_when_flag_absent(isolated_config):
    _write_default(isolated_config, "mock", "from-config")
    assert tui_main._resolve_model(None) == ("mock", "from-config")


def test_resolve_falls_back_to_hardcoded_when_no_config(isolated_config):
    assert tui_main._resolve_model(None) == ("vibeproxy", None)


# --- resolve YOLO: flag > pin > off ---

def test_resolve_yolo_flag_forces_on(isolated_config):
    # even with no pin, the flag wins
    assert tui_main._resolve_yolo(True) is True


def test_resolve_yolo_uses_pin_when_flag_absent(isolated_config):
    from harness import config
    config.update_default(backend="vibeproxy", model="x", yolo_pinned=True)
    assert tui_main._resolve_yolo(False) is True


def test_resolve_yolo_off_when_no_flag_no_pin(isolated_config):
    assert tui_main._resolve_yolo(False) is False


def test_main_passes_resolved_yolo_to_app(isolated_config, monkeypatch):
    from harness import config
    config.update_default(backend="mock", model="x", yolo_pinned=True)
    captured = {}

    class _FakeApp:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self):
            pass

    monkeypatch.setattr(tui_main, "HarnessTui", _FakeApp)
    monkeypatch.setattr(tui_main.paths, "load_env", lambda cwd: None)
    tui_main.main(["--model", "mock", "--cwd", str(isolated_config)])  # no --yolo flag
    assert captured["yolo"] is True            # picked up from the pin


def test_main_yolo_flag_overrides_absent_pin(isolated_config, monkeypatch):
    captured = {}

    class _FakeApp:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self):
            pass

    monkeypatch.setattr(tui_main, "HarnessTui", _FakeApp)
    monkeypatch.setattr(tui_main.paths, "load_env", lambda cwd: None)
    tui_main.main(["--model", "mock", "--cwd", str(isolated_config), "--yolo"])
    assert captured["yolo"] is True


# --- effective worker model id (the label the TUI footer shows) ---

def test_effective_worker_model_id_mock_is_none():
    # mock backend always returns None regardless of persona or shell_set_model
    assert tui_main._effective_worker_model_id("mock", None, False) is None


def test_effective_worker_model_id_vibeproxy_uses_env(monkeypatch):
    # A real shell VIBEPROXY_MODEL (shell_set_model=True) wins for all personas
    monkeypatch.setenv("VIBEPROXY_MODEL", "claude-opus-4-8")
    assert tui_main._effective_worker_model_id("vibeproxy", None, shell_set_model=True) == "claude-opus-4-8"


def test_effective_worker_model_id_vibeproxy_default_when_env_unset(monkeypatch, isolated_config):
    # No shell env, no .env, no persisted config: falls back to engine default
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    assert tui_main._effective_worker_model_id("vibeproxy", None, shell_set_model=False) == "gpt-5.4"


def test_main_seeds_worker_model_id_from_persisted_model(isolated_config, monkeypatch):
    """A fresh launch with no --model flag seeds HarnessTui.worker_model_id from
    the persisted done.conf model, so the footer shows the real id (not the
    'default model' fallback) after logout/login."""
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    _write_default(isolated_config, "vibeproxy", "claude-opus-4-8")
    captured = {}

    class _FakeApp:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self):
            pass

    monkeypatch.setattr(tui_main, "HarnessTui", _FakeApp)
    monkeypatch.setattr(tui_main.paths, "load_env", lambda cwd: None)

    tui_main.main(["--cwd", str(isolated_config)])  # no --model -> use persisted

    assert captured["model"] == "vibeproxy"
    assert captured["worker_model_id"] == "claude-opus-4-8"


# --- precedence: done.conf beats a .env VIBEPROXY_MODEL, but a real shell env wins ---

def _run_main_capturing(monkeypatch, isolated_config, dotenv_model):
    """Run main() with no --model flag. `dotenv_model`, if not None, simulates a
    project .env setting VIBEPROXY_MODEL the way load_env does (override=False:
    only fills it in when not already a real shell env var). Returns the kwargs
    HarnessTui was constructed with."""
    captured = {}

    class _FakeApp:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self):
            pass

    def fake_load_env(cwd):
        if dotenv_model is not None:
            os.environ.setdefault("VIBEPROXY_MODEL", dotenv_model)  # .env never beats a real shell env

    monkeypatch.setattr(tui_main, "HarnessTui", _FakeApp)
    monkeypatch.setattr(tui_main.paths, "load_env", fake_load_env)
    tui_main.main(["--cwd", str(isolated_config)])
    return captured


def test_persisted_model_beats_dotenv(isolated_config, monkeypatch):
    """A .env VIBEPROXY_MODEL must NOT override the interactively-persisted model.
    This is the logout/login bug: .env had gpt-5.4 and silently won.
    Under the new design the persisted model is resolved via resolve_session_model
    (not exported into VIBEPROXY_MODEL) so the footer reflects done.conf directly."""
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)  # no real shell env
    _write_default(isolated_config, "vibeproxy", "claude-opus-4-8")

    captured = _run_main_capturing(monkeypatch, isolated_config, dotenv_model="gpt-5.4")

    # done.conf model wins; no longer exported into os.environ
    assert captured["worker_model_id"] == "claude-opus-4-8"


def test_real_shell_env_beats_persisted_model(isolated_config, monkeypatch):
    """An explicitly-exported shell VIBEPROXY_MODEL outranks done.conf."""
    monkeypatch.setenv("VIBEPROXY_MODEL", "shell-override")  # real shell env present
    _write_default(isolated_config, "vibeproxy", "claude-opus-4-8")

    captured = _run_main_capturing(monkeypatch, isolated_config, dotenv_model="gpt-5.4")

    assert os.environ["VIBEPROXY_MODEL"] == "shell-override", "real shell env wins over done.conf"
    assert captured["worker_model_id"] == "shell-override"


def test_persisted_model_applied_when_no_env_at_all(isolated_config, monkeypatch):
    """No shell env, no .env: the persisted model resolves from done.conf.
    Under the new design it is resolved via resolve_session_model, not exported
    into VIBEPROXY_MODEL."""
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    _write_default(isolated_config, "vibeproxy", "claude-opus-4-8")

    captured = _run_main_capturing(monkeypatch, isolated_config, dotenv_model=None)

    # done.conf model flows to footer; no longer exported into os.environ
    assert captured["worker_model_id"] == "claude-opus-4-8"


# --- Task 7: --persona resolves per-persona model + flows to agent cmd & /reload ---

def test_resolve_model_reads_named_persona_key(isolated_config, monkeypatch):
    from harness import tui_main, config
    config.save_agent("fred", config.AgentConfig(backend="vibeproxy", model="m-fred"))
    backend, model = tui_main._resolve_model(None, "fred")
    assert (backend, model) == ("vibeproxy", "m-fred")

def test_resolve_model_default_persona_unchanged(isolated_config, monkeypatch):
    from harness import tui_main, config
    config.save_default(config.AgentConfig(backend="vibeproxy", model="m-def"))
    assert tui_main._resolve_model(None, "default") == ("vibeproxy", "m-def")
    assert tui_main._resolve_model(None, None) == ("vibeproxy", "m-def")

def test_persona_flows_into_agent_cmd_and_relaunch(isolated_config, monkeypatch, tmp_path):
    from harness import tui_main, paths
    (paths.config_dir() / "agents" / "fred").mkdir(parents=True)
    captured = {}
    monkeypatch.setattr(tui_main, "HarnessTui",
        lambda **kw: captured.update(kw) or type("A", (), {"run": lambda self: None, "_reexec": False})())
    tui_main.main(["--model", "vibeproxy", "--cwd", str(tmp_path), "--persona", "fred"])
    assert "--persona" in captured["agent_cmd"]
    assert "fred" in captured["agent_cmd"]


def test_relaunch_args_always_emits_model_flag(tmp_path):
    """_relaunch_args must ALWAYS emit --model (the session backend: mock vs vibeproxy)
    so the backend survives a re-exec."""
    from types import SimpleNamespace as NS
    args = NS(model="vibeproxy", yolo=False, persona="fred")
    flags = tui_main._relaunch_args(args, str(tmp_path))
    assert "--model" in flags
    assert "vibeproxy" in flags
    assert "--persona" in flags and "fred" in flags
