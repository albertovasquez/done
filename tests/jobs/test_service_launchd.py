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
    # build_plist resolves the `harness-cron` console-script next to the interpreter
    # (the `-m harness.jobs.cron_main` form skips the __main__ guard and returns
    # immediately). ProgramArguments is that binary, derived from the given python.
    assert doc["ProgramArguments"] == ["/usr/bin/harness-cron"]


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


def test_service_status_not_installed_when_no_plist(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    res = L.service_status()                             # no plist on disk
    assert res.state == "not-installed"


def test_service_status_installed_when_plist_loaded(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    L.plist_path().parent.mkdir(parents=True, exist_ok=True)
    L.plist_path().write_bytes(b"<plist/>")
    monkeypatch.setattr(L, "_run", lambda argv: (0, ""))  # launchctl print succeeds
    res = L.service_status()
    assert res.state == "installed"


def test_service_status_orphaned_plist_is_not_installed(monkeypatch, tmp_path):
    # Plist on disk but `launchctl print` fails (orphaned — file present, not loaded).
    # service_status MUST NOT report "installed" here: _decide_cron_autostart treats
    # only "installed" as "OS owns the daemon, do nothing", so a false "installed"
    # would silently skip autostart and jobs would never fire. Return "not-installed"
    # so the boot path falls through to the prompt/fallback-spawn; the orphaned
    # nuance is preserved in the detail string.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    L.plist_path().parent.mkdir(parents=True, exist_ok=True)
    L.plist_path().write_bytes(b"<plist/>")
    monkeypatch.setattr(L, "_run", lambda argv: (1, "Could not find service"))
    res = L.service_status()
    assert res.state == "not-installed", res
    assert "not loaded" in res.detail
