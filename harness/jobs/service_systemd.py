# harness/jobs/service_systemd.py
"""Linux systemd-user backend for the harness-cron daemon.

Writes a user unit (~/.config/systemd/user/harness-cron.service) with
Restart=always + WantedBy=default.target, enables it, and calls
`loginctl enable-linger` so the user service survives logout and reboot (the
detail OpenClaw/Hermes both apply). Shell-outs go through _run for testability.
"""
from __future__ import annotations

import getpass
import subprocess
import sys
from pathlib import Path

from harness.jobs.service import ServiceResult

UNIT = "harness-cron.service"


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT


def build_unit(python: str) -> str:
    return (
        "[Unit]\n"
        "Description=DoneDone cron daemon (harness-cron)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={python} -m harness.jobs.cron_main\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _run(argv: list[str]) -> tuple[int, str]:
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, (p.stderr or p.stdout).strip()


def install() -> ServiceResult:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_unit(sys.executable), encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    rc, err = _run(["systemctl", "--user", "enable", "--now", UNIT])
    if rc != 0:
        return ServiceResult(False, "systemd", "error",
                             f"systemctl --user enable failed: {err}")
    # Best-effort: lingering lets the user service run without an active login
    # session (survives logout/reboot). Failure is non-fatal — log via detail.
    rc_l, err_l = _run(["loginctl", "enable-linger", getpass.getuser()])
    linger = "linger on" if rc_l == 0 else f"linger unavailable ({err_l})"
    return ServiceResult(True, "systemd", "installed",
                         f"systemd user service enabled; {linger}")


def uninstall() -> ServiceResult:
    path = unit_path()
    if not path.exists():
        return ServiceResult(True, "systemd", "not-installed", "no systemd unit to remove")
    _run(["systemctl", "--user", "disable", "--now", UNIT])
    try:
        path.unlink()
    except OSError:
        pass
    _run(["systemctl", "--user", "daemon-reload"])
    return ServiceResult(True, "systemd", "not-installed", "systemd user service removed")


def service_status() -> ServiceResult:
    if not unit_path().exists():
        return ServiceResult(True, "systemd", "not-installed", "systemd unit not installed")
    rc, out = _run(["systemctl", "--user", "is-active", UNIT])
    return ServiceResult(True, "systemd", "installed", f"systemd unit active={out or 'unknown'}")
