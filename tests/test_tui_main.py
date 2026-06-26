import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

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
