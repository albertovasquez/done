import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

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


def _patch_common(monkeypatch, reexec):
    """Patch HarnessTui to a fake whose _reexec is controllable, and stub the
    env/path side effects so main() can run headless."""
    app = _FakeApp(reexec)
    monkeypatch.setattr(tui_main, "HarnessTui", lambda **kw: app)
    monkeypatch.setattr(tui_main.paths, "load_env", lambda cwd: None)
    return app


def test_main_reexecs_when_flag_set(monkeypatch, tmp_path):
    app = _patch_common(monkeypatch, reexec=True)
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
    app = _patch_common(monkeypatch, reexec=False)
    called = {"execv": False}
    monkeypatch.setattr(tui_main.os, "execv",
                        lambda *a: called.__setitem__("execv", True))
    tui_main.main(["--model", "mock", "--cwd", str(tmp_path)])
    assert app.ran is True
    assert called["execv"] is False, "no re-exec when _reexec is False"


def test_main_reexec_oserror_exits_nonzero(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, reexec=True)
    def boom(path, argv):
        raise OSError("no such file")
    monkeypatch.setattr(tui_main.os, "execv", boom)
    with pytest.raises(SystemExit) as ei:
        tui_main.main(["--model", "mock", "--cwd", str(tmp_path)])
    assert ei.value.code == 1
    assert "reload failed to re-exec" in capsys.readouterr().err
