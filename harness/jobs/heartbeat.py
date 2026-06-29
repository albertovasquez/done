"""Liveness signal for the harness-cron daemon — heartbeat files + classifier.

Borrowed from Hermes Agent (cron/jobs.py): the daemon writes two epoch files
(alive, last-clean-tick); the panel reads their age and classifies. Best-effort
writes never break the tick loop; reads never raise into the TUI.

Paths are computed at call time via cron_dir() (never cached at import) so a
test patching harness.paths.config_dir redirects them — mirrors paths.py/store.py.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from harness.jobs import paths as _paths


def _heartbeat_file() -> Path:
    return _paths.cron_dir() / "ticker_heartbeat"


def _success_file() -> Path:
    return _paths.cron_dir() / "ticker_success"


def _atomic_write_epoch(path: Path, now: float) -> None:
    # Mirrors harness/jobs/store.py _save: tmp + os.replace (atomic for a one-line file).
    _paths.cron_dir().mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f"{now}\n", encoding="utf-8")
    os.replace(tmp, path)


def record_heartbeat(success: bool = False) -> None:
    """Write the alive marker; if success, also the last-clean-tick marker.
    Best-effort: any failure is swallowed so the tick loop is never disrupted."""
    now = time.time()
    try:
        _atomic_write_epoch(_heartbeat_file(), now)
    except Exception:
        pass
    if success:
        try:
            _atomic_write_epoch(_success_file(), now)
        except Exception:
            pass


def _epoch_file_age(path: Path, now: float | None) -> float | None:
    if now is None:
        now = time.time()
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return max(0.0, now - float(raw))
    except Exception:
        return None


def heartbeat_age(now: float | None = None) -> float | None:
    return _epoch_file_age(_heartbeat_file(), now)


def success_age(now: float | None = None) -> float | None:
    return _epoch_file_age(_success_file(), now)


def daemon_status(hb_age: float | None, ok_age: float | None, *, interval: float) -> str:
    """Classify daemon liveness from two file ages. Pure; no I/O."""
    stale_after = interval * 3 + 20
    if hb_age is None:
        return "stopped"
    if hb_age > stale_after:
        return "stalled"
    if ok_age is None or ok_age > stale_after:
        return "failing"
    return "running"


def status_line(status: str, hb_age: float | None) -> str:
    """One-line header text for the panel. Color is applied by the widget."""
    if status == "running":
        return "✓ daemon running — jobs will fire"
    if status == "failing":
        return "⚠ daemon running but ticks are failing"
    if status == "stalled":
        n = 0 if hb_age is None else int(hb_age)
        return f"⚠ daemon stalled — no heartbeat for {n}s"
    return "✗ daemon not running — scheduled jobs won't fire"
