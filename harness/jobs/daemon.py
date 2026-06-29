# harness/jobs/daemon.py
"""Daemon loop: due-job selection, per-tick execution, async run_forever."""
from __future__ import annotations

import logging

from harness.jobs import ops
from harness.jobs import model as m
from harness.jobs.executor import OrphanPersona, run_headless_turn
from harness.jobs.heartbeat import record_heartbeat

logger = logging.getLogger(__name__)

# Single source for the daemon tick cadence — referenced by run_forever's default,
# cron_main's argparse default, and the panel's staleness threshold.
DEFAULT_INTERVAL = 30.0


def due_jobs(jobs: list[m.Job], *, now: float) -> list[m.Job]:
    """Return jobs that are enabled and whose next_run_at <= now."""
    return [
        j for j in jobs
        if j.enabled
        and j.state.next_run_at is not None
        and j.state.next_run_at <= now
    ]


def tick(now: float, *, executor=None) -> list[str]:
    """Fire all due jobs; disable orphan-persona jobs without recording a run.

    Returns the list of job ids that were processed (run or orphan-disabled).
    executor defaults to run_headless_turn (resolved at call time so tests can
    monkeypatch harness.jobs.daemon.run_headless_turn and have it take effect).
    """
    if executor is None:
        executor = run_headless_turn
    jobs = ops.list_jobs(include_disabled=False)
    fired: list[str] = []
    for job in due_jobs(jobs, now=now):
        try:
            ops.run(job.id, executor=executor, now=now)
        except OrphanPersona:
            # Persona no longer exists — disable without a run record.
            ops.update(job.id, now=now, enabled=False)
        fired.append(job.id)
    return fired


async def run_forever(
    *,
    interval: float = DEFAULT_INTERVAL,
    clock,
    sleep,
    executor=None,
) -> None:
    """Daemon loop: call tick(clock()) then await sleep(interval), forever.

    clock and sleep are injected so tests can control time and interrupt the loop.
    In production use: clock=time.time, sleep=asyncio.sleep.
    """
    if executor is None:
        executor = run_headless_turn
    # Seed BOTH heartbeat files so a freshly-started daemon reads "running", not
    # "failing", during the first-tick window (before any tick has succeeded).
    record_heartbeat(success=True)
    while True:
        now = clock()
        try:
            tick(now, executor=executor)
        except Exception:  # noqa: BLE001 — a transient tick failure must not kill the loop
            # KeyboardInterrupt / CancelledError are NOT Exception, so they still
            # propagate and the loop stays stoppable.
            logger.exception("cron tick failed at %s; continuing to next interval", now)
            record_heartbeat(success=False)  # alive but this tick failed
        else:
            record_heartbeat(success=True)   # clean tick
        await sleep(interval)
