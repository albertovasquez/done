"""One-time marker: has the user been asked about cron autostart yet?

Stored as an empty file in cron_dir so the first-run opt-in prompt fires exactly
once, regardless of how many `dn` windows open. Paths resolved at call time so
tests redirect via config_dir.
"""
from __future__ import annotations

from harness.jobs.paths import cron_dir


def _marker():
    return cron_dir() / ".service_prompt_done"


def has_been_asked() -> bool:
    return _marker().exists()


def mark_asked() -> None:
    cron_dir().mkdir(parents=True, exist_ok=True)
    _marker().touch()
