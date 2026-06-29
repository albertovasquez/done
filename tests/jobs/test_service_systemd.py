# tests/jobs/test_service_systemd.py
from pathlib import Path
import pytest
from harness.jobs import service_systemd as S


def test_unit_name():
    assert S.UNIT == "harness-cron.service"


def test_unit_path_under_user_systemd(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert S.unit_path() == tmp_path / ".config" / "systemd" / "user" / "harness-cron.service"


def test_build_unit_has_restart_always_and_wantedby():
    unit = S.build_unit(python="/usr/bin/python3")
    assert "Restart=always" in unit
    assert "WantedBy=default.target" in unit
    assert "ExecStart=/usr/bin/python3 -m harness.jobs.cron_main" in unit


def test_install_writes_unit_enables_and_lingers(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    calls = []
    monkeypatch.setattr(S, "_run", lambda argv: calls.append(argv) or (0, ""))
    res = S.install()
    assert res.ok is True and res.state == "installed"
    assert S.unit_path().is_file()
    flat = [" ".join(c) for c in calls]
    assert any("daemon-reload" in c for c in flat), flat
    assert any("enable" in c and "harness-cron" in c for c in flat), flat
    assert any("enable-linger" in c for c in flat), flat          # survives reboot


def test_uninstall_idempotent_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(S, "_run", lambda argv: (0, ""))
    res = S.uninstall()
    assert res.ok is True and res.state == "not-installed"


def test_service_status_not_installed_when_no_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    res = S.service_status()                              # no unit file
    assert res.state == "not-installed"


def test_service_status_installed_when_unit_active(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    S.unit_path().parent.mkdir(parents=True, exist_ok=True)
    S.unit_path().write_text("[Unit]\n")
    # `systemctl --user is-active` → rc 0, "active"
    monkeypatch.setattr(S, "_run", lambda argv: (0, "active"))
    res = S.service_status()
    assert res.state == "installed"


def test_service_status_orphaned_unit_is_not_installed(monkeypatch, tmp_path):
    # Unit file on disk but `systemctl --user is-active` reports it is NOT active
    # (inactive/failed — e.g. a stale unit, or systemd never enabled it). As with
    # launchd, service_status MUST NOT report "installed" here, or
    # _decide_cron_autostart would skip autostart and jobs would never fire.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    S.unit_path().parent.mkdir(parents=True, exist_ok=True)
    S.unit_path().write_text("[Unit]\n")
    monkeypatch.setattr(S, "_run", lambda argv: (3, "inactive"))  # is-active → nonzero
    res = S.service_status()
    assert res.state == "not-installed", res
    assert "inactive" in res.detail or "not active" in res.detail
