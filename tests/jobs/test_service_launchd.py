# tests/jobs/test_service_launchd.py
import plistlib
from pathlib import Path
import pytest
from harness.jobs import service_launchd as L


def test_label_is_reverse_dns():
    assert L.LABEL == "com.quiubo.done.cron"


def test_plist_path_under_launchagents(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert L.plist_path() == tmp_path / "Library" / "LaunchAgents" / "com.quiubo.done.cron.plist"


def test_build_plist_has_runatload_keepalive_and_program():
    raw = L.build_plist(python="/usr/bin/python3", label="com.quiubo.done.cron")
    doc = plistlib.loads(raw)
    assert doc["Label"] == "com.quiubo.done.cron"
    assert doc["RunAtLoad"] is True
    assert doc["KeepAlive"] is True
    assert doc["ProgramArguments"] == ["/usr/bin/python3", "-m", "harness.jobs.cron_main"]


def test_build_plist_has_throttle_interval():
    # KeepAlive respawns instantly on exit; ThrottleInterval rate-limits a tight
    # respawn loop (e.g. a daemon that crashes immediately on startup). The standard
    # launchd guard — mirrors systemd's RestartSec=5 on the Linux backend.
    doc = plistlib.loads(L.build_plist(python="/usr/bin/python3", label="com.quiubo.done.cron"))
    assert doc["ThrottleInterval"] == 10


def test_install_writes_plist_and_bootstraps(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    calls = []
    monkeypatch.setattr(L, "_run", lambda argv: calls.append(argv) or (0, ""))
    res = L.install()
    assert res.ok is True and res.state == "installed"
    assert L.plist_path().is_file()                      # plist written
    assert any("bootstrap" in c for c in calls), calls   # launchctl bootstrap invoked


def test_uninstall_is_idempotent_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(L, "_run", lambda argv: (0, ""))
    res = L.uninstall()                                  # nothing installed
    assert res.ok is True and res.state == "not-installed"
