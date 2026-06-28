"""CronDashboard — read-mostly roster widget for scheduled cron jobs.

One row per job: ● {name} · {status_word} · next {nxt}

Actions:
  action_run_now(job_id)       — trigger an immediate run via ops.run
  action_toggle_enabled(job_id) — flip enabled flag via ops.update
  action_remove(job_id)        — delete job via ops.remove
  action_new_job()             — post NewJobRequested so the app can invoke
                                 the create-job skill

Pattern: mirrors AgentRail (agent_rail.py:61) — subclasses ListView, holds rows
in a tuple, sets rows via set_rows(), carries row data on ListItem.data, posts
a Message for app-level wiring. Pure render function is separately unit-tested.
"""

from __future__ import annotations

import time
import logging

from textual import on
from textual.binding import Binding
from textual.message import Message
from textual.widgets import ListItem, ListView, Static

from harness.jobs import model as m, ops

logger = logging.getLogger(__name__)


# ── Pure render function (unit-tested) ────────────────────────────────────────

def render_rows(jobs: list[m.Job]) -> list[str]:
    """Return one display string per job.

    Format: ● {name} · {status_word} · next {nxt}

    status_word priority:
      1. "running"  — state.running_since is set (may coexist with disabled)
      2. "disabled" — enabled is False
      3. "scheduled"

    nxt:
      "—"           — next_run_at is None
      "@{int(t)}"   — formatted epoch second
    """
    rows: list[str] = []
    for job in jobs:
        if job.state.running_since is not None:
            status_word = "running"
        elif not job.enabled:
            status_word = "disabled"
        else:
            status_word = "scheduled"

        nra = job.state.next_run_at
        nxt = "—" if nra is None else f"@{int(nra)}"

        rows.append(f"● {job.name} · {status_word} · next {nxt}")
    return rows


# ── Messages ──────────────────────────────────────────────────────────────────

class NewJobRequested(Message):
    """Posted when the user presses 'n' to create a new cron job."""


class JobActionFailed(Message):
    """Posted when a job action raises (job gone, executor error, etc.)."""
    def __init__(self, job_id: str, action: str, error: str) -> None:
        self.job_id = job_id
        self.action = action
        self.error = error
        super().__init__()


# ── Widget ────────────────────────────────────────────────────────────────────

class CronDashboard(ListView):
    """A selectable cron-job list. Rows set via set_rows(); row item carries
    the job id on .data. Action bindings let the user run/toggle/remove jobs.

    Mirrors AgentRail (agent_rail.py:61): ListView subclass, ListItem per row,
    item.data = job id. App-level wiring: handle NewJobRequested to invoke the
    create-job skill; handle JobActionFailed to surface errors.
    """

    BINDINGS = [
        Binding("n", "new_job", "New job"),
        Binding("r", "run_now", "Run now"),
        Binding("t", "toggle_enabled", "Toggle enabled"),
        Binding("backspace", "remove_job", "Remove job"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._jobs: list[m.Job] = []

    def set_rows(self, jobs: list[m.Job]) -> None:
        """Re-render the list with a new job snapshot."""
        self._jobs = list(jobs)
        self.clear()
        for row_text, job in zip(render_rows(jobs), jobs):
            item = ListItem(Static(row_text, markup=False))
            item.data = job.id           # carry job id (mirrors AgentRail pattern)
            self.append(item)

    def _focused_job_id(self) -> str | None:
        """Return the job id of the currently highlighted item, or None."""
        highlighted = self.highlighted_child
        if highlighted is None:
            return None
        return getattr(highlighted, "data", None)

    # ── actions ──────────────────────────────────────────────────────────────

    def action_new_job(self) -> None:
        self.post_message(NewJobRequested())

    def action_run_now(self) -> None:
        job_id = self._focused_job_id()
        if job_id is None:
            return
        from harness.jobs.executor import run_headless_turn
        try:
            ops.run(job_id, executor=run_headless_turn, now=time.time(), force=True)
        except KeyError:
            self.post_message(JobActionFailed(job_id, "run_now", "job not found"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_now failed for %s", job_id)
            self.post_message(JobActionFailed(job_id, "run_now", str(exc)))
        else:
            self._reload()

    def action_toggle_enabled(self) -> None:
        job_id = self._focused_job_id()
        if job_id is None:
            return
        job = ops.get(job_id)
        if job is None:
            self.post_message(JobActionFailed(job_id, "toggle_enabled", "job not found"))
            return
        try:
            ops.update(job_id, now=time.time(), enabled=not job.enabled)
        except Exception as exc:  # noqa: BLE001
            self.post_message(JobActionFailed(job_id, "toggle_enabled", str(exc)))
        else:
            self._reload()

    def action_remove_job(self) -> None:
        job_id = self._focused_job_id()
        if job_id is None:
            return
        try:
            ops.remove(job_id)
        except Exception as exc:  # noqa: BLE001
            self.post_message(JobActionFailed(job_id, "remove", str(exc)))
        else:
            self._reload()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _reload(self) -> None:
        """Refresh the list from the live store."""
        self.set_rows(ops.list_jobs())

    def refresh_jobs(self) -> None:
        """Public method: reload from the live store (call from a Timer or watcher)."""
        self._reload()
