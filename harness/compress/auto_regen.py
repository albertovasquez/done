"""Session-end consumer: refresh stale EXISTING compressed siblings, detached.

Registered for the `session_end` hook at import. On fire it finds stale existing
siblings (per-persona compress_aware gating + presence=opt-in are enforced by
targets), and if any exist spawns a detached worker to rebuild exactly those —
so quitting the TUI is never blocked by an LLM call. Never raises, never
surfaces an error to the user; a failed/interrupted regen just leaves the
sibling stale (self-heals next session)."""
from __future__ import annotations

import logging
import subprocess
import sys

from harness import hooks, paths
from harness.compress import targets

logger = logging.getLogger(__name__)


def _spawn_worker(paths_list: list[str]) -> None:
    """Spawn the detached regen worker for *paths_list*. Mirrors jobs/supervisor.py."""
    log_dir = paths.config_dir() / "compress-cache"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_fd = open(log_dir / "regen.log", "a")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "harness.compress.regen_worker", *paths_list],
            start_new_session=True,             # survives parent (TUI) exit
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            close_fds=True,
        )
    finally:
        log_fd.close()


def on_session_end(*, tracer=None, cwd=None, persona_id=None, **_) -> None:
    """Hook handler. Finds stale existing siblings; spawns the detached worker."""
    try:
        stale = targets.stale_existing_siblings(cwd=cwd)
    except Exception:
        logger.exception("auto_regen: discovery failed")
        return
    if not stale:
        return
    paths_list = [str(p) for p in stale]
    try:
        _spawn_worker(paths_list)
    except Exception as e:
        logger.exception("auto_regen: spawn failed")
        if tracer is not None:
            try:
                tracer.emit("dn", "compress.regen.spawn_failed", error=str(e))
            except Exception:
                logger.exception("tracer.emit failed")
        return
    if tracer is not None:
        try:
            tracer.emit("dn", "compress.regen.spawn", count=len(paths_list))
        except Exception:
            logger.exception("tracer.emit failed")


hooks.register("session_end", on_session_end, label="compress.auto_regen")
