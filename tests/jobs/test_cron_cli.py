# tests/jobs/test_cron_cli.py
import pytest
from harness.jobs import cli
from harness.jobs.service import ServiceResult


def test_install_routes_to_service(monkeypatch, capsys):
    monkeypatch.setattr(cli.service, "install",
                        lambda: ServiceResult(True, "launchd", "installed", "loaded"))
    rc = cli.run(["install"])
    assert rc == 0
    assert "installed" in capsys.readouterr().out.lower()


def test_status_routes_to_service(monkeypatch, capsys):
    monkeypatch.setattr(cli.service, "service_status",
                        lambda: ServiceResult(True, "systemd", "not-installed", "not installed"))
    rc = cli.run(["status"])
    assert rc == 0
    assert "not" in capsys.readouterr().out.lower()


def test_error_result_returns_exit_1(monkeypatch):
    monkeypatch.setattr(cli.service, "install",
                        lambda: ServiceResult(False, "launchd", "error", "boom"))
    assert cli.run(["install"]) == 1


def test_unknown_subcommand_returns_2(capsys):
    assert cli.run(["frobnicate"]) == 2
