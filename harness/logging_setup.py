"""One place to route the harness's `logger.warning`/`logger.exception` calls to
a file when --debug is on. Without this, those logs go to Python's default
handler (stderr, WARNING+) — invisible for the agent subprocess, whose stderr is
hidden behind the TUI's alt-screen. Under --debug they land next to the trace.

Idempotent: a process calls setup_file_logging once; a second call with the same
path is a no-op (so reconnects/re-execs don't stack handlers)."""

from __future__ import annotations

import logging
from pathlib import Path

_HANDLER_TAG = "_harness_debug_file"


def setup_file_logging(log_path: str | Path, level: int = logging.DEBUG) -> None:
    """Attach a DEBUG file handler to the `harness` logger namespace, writing to
    log_path. Creates the parent dir. Idempotent per (logger, path)."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("harness")
    # don't stack a second handler for the same file across reconnects
    for h in root.handlers:
        if getattr(h, _HANDLER_TAG, None) == str(log_path):
            return
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    setattr(handler, _HANDLER_TAG, str(log_path))
    root.addHandler(handler)
    # let DEBUG records through to our handler regardless of root config
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
