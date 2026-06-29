# harness/jobs/service_launchd.py
"""macOS launchd backend for the harness-cron daemon.

Writes a LaunchAgent plist with RunAtLoad + KeepAlive (so the daemon starts at
login and is restarted on crash — the OpenClaw/Hermes pattern) and loads it via
`launchctl bootstrap gui/<uid>`. All shell-outs go through _run so tests stub
them; plist content and paths are pure functions.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from harness.jobs.service import ServiceResult

LABEL = "com.quiubo.done.cron"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(python: str, label: str) -> bytes:
    # Resolve the harness-cron console-script binary next to the python interpreter.
    # Using the binary (which calls main()) is correct; `-m harness.jobs.cron_main`
    # skips the `if __name__ == "__main__"` guard and returns immediately.
    from pathlib import Path as _Path
    cron_bin = str(_Path(python).parent / "harness-cron")
    doc = {
        "Label": label,
        "ProgramArguments": [cron_bin],
        "RunAtLoad": True,
        "KeepAlive": True,
        # Rate-limit respawns: KeepAlive restarts the daemon the instant it exits,
        # so a daemon that fails immediately would otherwise hot-loop launchd.
        # 10s mirrors the systemd backend's RestartSec=5 intent. The PID lock
        # (harness/jobs/lock.py) is orthogonal and safe here: on SIGKILL the old
        # process is dead before respawn, so the new daemon's dead-pid reclaim
        # takes the lock rather than exiting — no acquire/exit/respawn loop.
        "ThrottleInterval": 10,
        "ProcessType": "Background",
    }
    return plistlib.dumps(doc)


def _run(argv: list[str]) -> tuple[int, str]:
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, (p.stderr or p.stdout).strip()


def _domain_target() -> str:
    return f"gui/{os.getuid()}"


def install() -> ServiceResult:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_plist(sys.executable, LABEL))
    # Idempotent reload: bootout first (ignore failure if not loaded), then bootstrap.
    _run(["launchctl", "bootout", _domain_target(), str(path)])
    rc, err = _run(["launchctl", "bootstrap", _domain_target(), str(path)])
    if rc != 0:
        return ServiceResult(False, "launchd", "error",
                             f"launchctl bootstrap failed: {err}")
    return ServiceResult(True, "launchd", "installed",
                         f"launchd service loaded ({path})")


def uninstall() -> ServiceResult:
    path = plist_path()
    if not path.exists():
        return ServiceResult(True, "launchd", "not-installed", "no launchd service to remove")
    _run(["launchctl", "bootout", _domain_target(), str(path)])
    try:
        path.unlink()
    except OSError:
        pass
    return ServiceResult(True, "launchd", "not-installed", "launchd service removed")


def service_status() -> ServiceResult:
    if not plist_path().exists():
        return ServiceResult(True, "launchd", "not-installed", "launchd service not installed")
    rc, _ = _run(["launchctl", "print", f"{_domain_target()}/{LABEL}"])
    if rc == 0:
        return ServiceResult(True, "launchd", "installed", "launchd service loaded")
    # Orphaned plist: the file is on disk but launchctl can't find the loaded job
    # (a manually-deleted registration, or a stale plist after a crash). Report
    # "not-installed", NOT "installed" — _decide_cron_autostart treats only
    # "installed" as "the OS owns the daemon, do nothing", so a false "installed"
    # here would silently skip autostart and jobs would never fire. The detail
    # preserves the nuance for `dn cron status`; `dn cron install` re-bootstraps it.
    return ServiceResult(True, "launchd", "not-installed", "plist present but not loaded")
