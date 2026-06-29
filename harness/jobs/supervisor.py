"""Ensure a single harness-cron daemon is running — called from the TUI on boot.

DELIBERATE BEHAVIOR: this spawns a DETACHED background process that OUTLIVES the
`done` window that started it (and every other window). Always-on, no config
switch — documented here and in the README. If a headless/server use-case appears
(running `done` on a box where a per-user background daemon is unwanted), that is
the first thing to revisit (a [jobs] autostart config key). The daemon itself is
single-instance via harness/jobs/lock.py, so a race between two windows can never
produce two daemons.
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
