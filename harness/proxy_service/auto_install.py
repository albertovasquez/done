"""Session-start consumer: auto-install the proxy when config.yaml is missing.

Registered for the `session_start` hook at import. Fires unconditionally on
every session start; only acts when config_gen.config_drift() reports
"missing" (proxy never installed — safe, no running process to disturb).
Never acts on "drifted" (already installed, just stale) — that case is
warn-only (see lifecycle.status() and tui/app.py::on_mount), because an
already-running proxy is a machine-global service other sessions/cron may
depend on; auto-restarting it here would be unsafe. Spawns a detached
`dn proxy install`, mirroring harness/compress/auto_regen.py: never blocks
session startup, never raises past this handler, self-heals next session on
failure."""
from __future__ import annotations

import logging
import subprocess
import sys

from harness import hooks
from harness.proxy_service import config_gen, paths

logger = logging.getLogger(__name__)


def _spawn_install() -> None:
    """Spawn `dn proxy install` detached. Mirrors auto_regen._spawn_worker."""
    log_dir = paths.data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_fd = open(log_dir / "auto-install.log", "a")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "harness.tui_main", "proxy", "install"],
            start_new_session=True,             # survives parent (TUI) exit
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            close_fds=True,
        )
    finally:
        log_fd.close()


def on_session_start(*, tracer=None, cwd=None, persona_id=None, **_) -> None:
    """Hook handler. Spawns a detached install only when config is missing."""
    try:
        drift = config_gen.config_drift()
    except Exception:
        logger.exception("auto_install: drift check failed")
        return
    if drift != "missing":
        return
    try:
        _spawn_install()
    except Exception as e:
        logger.exception("auto_install: spawn failed")
        if tracer is not None:
            try:
                tracer.emit("dn", "proxy.auto_install.spawn_failed", error=str(e))
            except Exception:
                logger.exception("tracer.emit failed")
        return
    if tracer is not None:
        try:
            tracer.emit("dn", "proxy.auto_install.spawn")
        except Exception:
            logger.exception("tracer.emit failed")


hooks.register("session_start", on_session_start, label="proxy.auto_install")
