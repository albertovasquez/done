"""Best-effort FALLBACK daemon spawn — used only when the OS service is absent.

PRIMARY autostart is the OS service manager (harness/jobs/service.py): launchd on
macOS, systemd-user on Linux, registered via `dn cron install`. When that service
is installed, the OS owns the daemon's lifecycle (autostart-at-boot, restart-on-
crash, single-instance) and this module is NOT used.

This fallback exists for users who declined the service (or are on an unsupported
platform): the TUI calls ensure_daemon_running() on boot to spawn a DETACHED
background daemon that outlives the window. It is single-instance via
harness/jobs/lock.py, so it can never produce two daemons. Unlike the OS service,
it does NOT survive a reboot or restart on crash — that is the gap `dn cron
install` closes.
"""
from __future__ import annotations

import logging
import subprocess
import sys

from harness.jobs import heartbeat
from harness.jobs import daemon
from harness.jobs.paths import cron_dir

logger = logging.getLogger(__name__)


def _spawn_detached() -> None:
    cron_dir().mkdir(parents=True, exist_ok=True)
    # Open the log fd, hand it to the child, then CLOSE it in the parent — Popen
    # dups it into the child, so the parent must not leak its own handle.
    log_fd = open(cron_dir() / "daemon.log", "a")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "harness.jobs.cron_main"],
            start_new_session=True,           # POSIX setsid → survives parent exit
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            close_fds=True,
        )
    finally:
        log_fd.close()


def ensure_daemon_running(*, spawn=_spawn_detached, now=None) -> str:
    """Spawn a detached daemon unless the heartbeat shows one already running.
    Best-effort: a spawn failure is logged, never raised. Returns a status word
    ("already-running" | "spawned" | "failed")."""
    status = heartbeat.daemon_status(
        heartbeat.heartbeat_age(now), heartbeat.success_age(now),
        interval=daemon.DEFAULT_INTERVAL,
    )
    if status == "running":
        return "already-running"
    try:
        spawn()
        return "spawned"
    except Exception:
        logger.exception("cron autostart spawn failed")
        return "failed"
