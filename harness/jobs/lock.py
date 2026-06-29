"""Single-instance lock for the harness-cron daemon.

The daemon claims cron/daemon.lock atomically (O_CREAT|O_EXCL) at startup and
holds it for its lifetime — so `harness-cron` run twice (by hand, by launchd, by
two `done` windows) yields exactly one live daemon. A crash leaves a stale lock
(dead pid) which the next daemon reclaims. Paths computed at call time via
cron_dir() so tests redirect via the config_dir patch (mirrors heartbeat.py).
"""
from __future__ import annotations

import os
from pathlib import Path

from harness.jobs import paths as _paths


def lock_file() -> Path:
    return _paths.cron_dir() / "daemon.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by another user — treat as alive
    return True


def _write_pid(path: Path, pid: int) -> None:
    # atomic replace so a concurrent reader never sees a torn pid
    tmp = path.with_suffix(".lock.tmp")
    tmp.write_text(f"{pid}\n", encoding="utf-8")
    os.replace(tmp, path)


def acquire(*, pid: int | None = None, pid_alive=_pid_alive) -> bool:
    """Claim the lock. Return True if we now own it, False if a live daemon holds it.

    `pid_alive` is bound as a default at def-time, so monkeypatching the module
    `_pid_alive` does NOT affect it — tests must INJECT `pid_alive=` (or seed a
    genuinely-live pid) to control the liveness check.
    """
    pid = pid if pid is not None else os.getpid()
    path = lock_file()
    _paths.cron_dir().mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # Someone holds it. Reclaim only if the stored owner is dead/garbled.
        try:
            owner = int(path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            owner = None
        if owner is not None and pid_alive(owner):
            return False
        # Reclaim. _write_pid is atomic (os.replace), but two daemons recovering
        # from the SAME crash can both reach here and both write — so confirm we
        # won by re-reading: exactly one pid survives as the last writer.
        _write_pid(path, pid)     # stale or unparseable → reclaim
        try:
            return int(path.read_text(encoding="utf-8").strip()) == pid
        except (ValueError, OSError):
            return False
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{pid}\n")
        return True


def release() -> None:
    """Best-effort unlink; no error if already gone."""
    try:
        lock_file().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
