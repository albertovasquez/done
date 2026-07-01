"""Pure view model for the agent jobs dashboard. No Textual. Maps the real
Job/JobState (harness.jobs) into flat JobRows the UI renders. Progress is always
None in Phase 1 — the backend exposes no fraction (verified)."""
from __future__ import annotations

from dataclasses import dataclass

from harness.jobs import model as m, ops


@dataclass(frozen=True)
class JobRow:
    name: str
    description: str
    status: str            # RUNNING|SCHEDULED|DISABLED|COMPLETED|FAILED|QUEUED
    progress: float | None # always None in P1 (no truthful source)
    when: str              # "in 2d 14h" | "" (running) | last-run relative
    elapsed: str            # "00:18:42" | "—"


def derive_status(job: m.Job, now: float) -> str:
    st = job.state
    if st.running_since is not None:
        return "RUNNING"
    if not job.enabled:
        return "DISABLED"
    if st.next_run_at is not None:
        if st.next_run_at <= now:
            return "QUEUED"           # due but not yet running (rare)
        return "SCHEDULED"
    if st.last_status == "error":
        return "FAILED"
    if st.last_status:
        return "COMPLETED"
    return "SCHEDULED"


def format_elapsed(running_since: float, now: float) -> str:
    s = max(0, int(now - running_since))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def format_when_scheduled(next_run_at: float, now: float) -> str:
    s = max(0, int(next_run_at - now))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600); mnt = rem // 60
    if d: return f"in {d}d {h}h"
    if h: return f"in {h}h {mnt}m"
    if mnt: return f"in {mnt}m"
    return "due"


def _row(job: m.Job, now: float) -> JobRow:
    status = derive_status(job, now)
    st = job.state
    if status == "RUNNING" and st.running_since is not None:
        when, elapsed = "", format_elapsed(st.running_since, now)
    elif status == "SCHEDULED" and st.next_run_at is not None:
        when, elapsed = format_when_scheduled(st.next_run_at, now), "—"
    elif status in ("COMPLETED", "FAILED") and st.last_duration is not None:
        when, elapsed = "", format_elapsed(now - st.last_duration, now)
    else:
        when, elapsed = "", "—"
    return JobRow(name=job.name, description=job.description, status=status,
                  progress=None, when=when, elapsed=elapsed)


def job_rows(agent_id: str, now: float) -> tuple[JobRow, ...]:
    # Only the store read is allowed to fail silently (missing/empty store → the
    # UI's empty state). The mapping below is pure — a bug there must NOT be
    # swallowed into a misleading "no jobs" render, so it stays outside the try.
    try:
        jobs = ops.list_jobs(agent_id=agent_id)
    except Exception:
        return ()
    return tuple(_row(j, now) for j in jobs)
