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


def test_help_returns_0(capsys):
    # #164: argparse raises SystemExit(0) for --help. run() must propagate that
    # exit code (help=0), not flatten every SystemExit to a usage-error 2.
    assert cli.run(["--help"]) == 0


def test_missing_subcommand_returns_2(capsys):
    # No action → argparse usage error → SystemExit(2). Still 2, not flattened to 0.
    assert cli.run([]) == 2
