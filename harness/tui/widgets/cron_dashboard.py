"""CronDashboard — read-mostly roster widget for scheduled cron jobs.

One row per job: ● {name} · {status_word} · {when}

Actions:
  action_run_now(job_id)       — trigger an immediate run via ops.run
  action_toggle_enabled(job_id) — flip enabled flag via ops.update
  action_request_remove(job_id) — ask to delete: posts JobRemoveRequested for the
                                  app to confirm (delete is destructive/irreversible;
                                  the widget never calls ops.remove directly — #178)
(creation is agent-native: ask the agent in chat, no dashboard key)

Pattern: mirrors AgentRail (agent_rail.py:61) — subclasses ListView, holds rows
in a tuple, sets rows via set_rows(), carries row data on ListItem.data, posts
a Message for app-level wiring. Pure render function is separately unit-tested.
"""

from __future__ import annotations

import time
import logging

from textual.binding import Binding
from textual.message import Message
from textual.widgets import ListItem, ListView, Static

from harness.jobs import model as m, ops
from harness.jobs import heartbeat as _hb
from harness.jobs import daemon as _daemon

logger = logging.getLogger(__name__)

_STATUS_COLOR = {"running": "green", "failing": "yellow", "stalled": "yellow", "stopped": "red"}


def daemon_header(hb_age: float | None, ok_age: float | None, *, interval: float) -> tuple[str, str]:
    """Pure: (header text, color name) for the current daemon liveness."""
    status = _hb.daemon_status(hb_age, ok_age, interval=interval)
    return _hb.status_line(status, hb_age), _STATUS_COLOR[status]


# ── Pure render helpers (unit-tested) ─────────────────────────────────────────

def _humanize_until(delta_seconds: float | None) -> str:
    """Render a seconds-until-next-run delta as a short human-readable string.

    Buckets:
      None      → "—"   (no next run scheduled)
      <= 0      → "due" (overdue / ready to run now)
      < 60s     → "<1m"
      < 3600s   → "in {m}m"
      < 86400s  → "in {h}h"
      else      → "in {d}d"
    """
    if delta_seconds is None:
        return "—"
    if delta_seconds <= 0:
        return "due"
    if delta_seconds < 60:
        return "<1m"
    if delta_seconds < 3600:
        return f"in {int(delta_seconds // 60)}m"
    if delta_seconds < 86400:
        return f"in {int(delta_seconds // 3600)}h"
    return f"in {int(delta_seconds // 86400)}d"


def render_rows(jobs: list[m.Job], now: float | None = None) -> list[str]:
    """Return one display string per job.

    Format: ● {name} · {status_word} · {when}

    status_word priority:
      1. "running"  — state.running_since is set (may coexist with disabled)
      2. "disabled" — enabled is False
      3. "scheduled"

    when: a human-readable relative time until next_run_at (see _humanize_until),
    measured against *now* (defaults to time.time() so live rows are current).
    Pass an explicit *now* for deterministic output in tests.
    """
    if now is None:
        now = time.time()
    rows: list[str] = []
    for job in jobs:
        if job.state.running_since is not None:
            status_word = "running"
        elif not job.enabled:
            status_word = "disabled"
        else:
            status_word = "scheduled"

        nra = job.state.next_run_at
        when = _humanize_until(None if nra is None else nra - now)

        rows.append(f"● {job.name} · {status_word} · {when}")
    return rows


# ── Messages ──────────────────────────────────────────────────────────────────

class JobActionFailed(Message):
    """Posted when a job action raises (job gone, executor error, etc.)."""
    def __init__(self, job_id: str, action: str, error: str) -> None:
        self.job_id = job_id
        self.action = action
        self.error = error
        super().__init__()


class JobRemoveRequested(Message):
    """Posted when the user asks to delete the focused job (the 'd' key).

    Delete is destructive and irreversible, so the widget never calls
    ops.remove itself (#178): it posts this, and the app confirms via a modal
    before removing. Carries the job name so the confirm prompt can be specific.
    """
    def __init__(self, job_id: str, name: str) -> None:
        self.job_id = job_id
        self.name = name
        super().__init__()


# ── Widget ────────────────────────────────────────────────────────────────────

class CronDashboard(ListView):
    """A selectable cron-job list. Rows set via set_rows(); row item carries
    the job id on .data. Action bindings let the user run/toggle/remove jobs.

    Mirrors AgentRail (agent_rail.py:61): ListView subclass, ListItem per row,
    item.data = job id. App-level wiring: handle JobActionFailed to surface
    errors. Job CREATION is agent-native — ask the agent in chat ("create a cron
    job that…"); the router loads the create-job skill. No dashboard create key.
    """

    BINDINGS = [
        Binding("r", "run_now", "Run now"),
        Binding("t", "toggle_enabled", "Toggle enabled"),
        # 'd' (not backspace) — backspace is the most-pressed correction key and
        # instantly deleting a live job on a reflex press caused real data loss
        # (#178). Delete now sits on a deliberate key and routes through a confirm.
        Binding("d", "request_remove", "Delete job"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._jobs: list[m.Job] = []
        # Bordered-panel title; the round border + this title give the roster
        # framed chrome (CSS #cron-dashboard) instead of bare floating rows.
        self.border_title = "CRON JOBS"

    def set_rows(self, jobs: list[m.Job]) -> None:
        """Re-render the list with a new job snapshot, daemon-status header first."""
        self._jobs = list(jobs)
        self.clear()
        # Daemon-liveness header: a non-selectable row above the roster. Read on
        # open (set_rows runs every time the drawer opens); no polling/timer.
        text, color = daemon_header(
            _hb.heartbeat_age(), _hb.success_age(), interval=_daemon.DEFAULT_INTERVAL
        )
        # Color travels with the text via Rich markup (testable, no CSS-per-status
        # needed). Static carries no .data, so action guards treat it as no focus.
        header = ListItem(Static(f"[{color}]{text}[/]", markup=True))
        header.disabled = True           # non-selectable; ListView skips disabled on highlight
        header.add_class("cron-daemon-status")
        self.append(header)
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

    def action_request_remove(self) -> None:
        """Ask to delete the focused job — post JobRemoveRequested for the app to
        confirm. The widget never calls ops.remove itself: delete is destructive
        and irreversible, so a bare key press must not be able to destroy a job
        (#178). The app pushes a confirm modal and only then removes."""
        job_id = self._focused_job_id()
        if job_id is None:
            return
        name = next((j.name for j in self._jobs if j.id == job_id), job_id)
        self.post_message(JobRemoveRequested(job_id, name))

    # ── internal helpers ──────────────────────────────────────────────────────

    def _reload(self) -> None:
        """Refresh the list from the live store."""
        self.set_rows(ops.list_jobs())

    def refresh_jobs(self) -> None:
        """Public method: reload from the live store (call from a Timer or watcher)."""
        self._reload()
