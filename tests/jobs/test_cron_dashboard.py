# tests/jobs/test_cron_dashboard.py
"""Pure unit tests for cron_dashboard.render_rows.

The Textual widget itself (CronDashboard) is not exercised here — it requires a
running Textual app. These tests pin the display-string contract for the pure
render function.
"""
from harness.jobs import model as m
from harness.tui.widgets.cron_dashboard import _humanize_until, render_rows

# Fixed reference "now" so relative-time rows are deterministic.
NOW = 1_700_000_000.0


def _job(**kw) -> m.Job:
    base = dict(
        id="j1", name="my-job", agent_id="fred",
        schedule=m.Every(seconds=60),
        payload=m.Reminder(text="hi"),
        grant=m.Grant(tools="inherit", paths="workspace",
                      write=False, exec=False, network=False),
        cost=m.CostGate(timeout_s=10, min_cadence_s=60, max_consecutive_failures=2),
        state=m.JobState(),
    )
    base.update(kw)
    return m.Job(**base)


# ── _humanize_until bucket boundaries ────────────────────────────────────────

def test_humanize_none():
    assert _humanize_until(None) == "—"


def test_humanize_overdue_is_due():
    assert _humanize_until(0) == "due"
    assert _humanize_until(-5) == "due"


def test_humanize_under_a_minute():
    assert _humanize_until(1) == "<1m"
    assert _humanize_until(59) == "<1m"


def test_humanize_minutes():
    assert _humanize_until(60) == "in 1m"
    assert _humanize_until(45 * 60) == "in 45m"
    assert _humanize_until(3599) == "in 59m"


def test_humanize_hours():
    assert _humanize_until(3600) == "in 1h"
    assert _humanize_until(8 * 3600) == "in 8h"
    assert _humanize_until(86399) == "in 23h"


def test_humanize_days():
    assert _humanize_until(86400) == "in 1d"
    assert _humanize_until(2 * 86400) == "in 2d"


# ── render_rows contract ────────────────────────────────────────────────────

def test_empty_list():
    assert render_rows([]) == []


def test_scheduled_with_next_run():
    # next_run_at is 8h out → "in 8h" (deterministic via explicit now=).
    job = _job(state=m.JobState(next_run_at=NOW + 8 * 3600))
    rows = render_rows([job], now=NOW)
    assert rows == ["● my-job · scheduled · in 8h"]


def test_scheduled_no_next_run():
    job = _job(state=m.JobState(next_run_at=None))
    rows = render_rows([job], now=NOW)
    assert rows == ["● my-job · scheduled · —"]


def test_overdue_next_run_is_due():
    job = _job(state=m.JobState(next_run_at=NOW - 100))
    rows = render_rows([job], now=NOW)
    assert rows == ["● my-job · scheduled · due"]


def test_disabled():
    job = _job(enabled=False, state=m.JobState(next_run_at=NOW + 8 * 3600))
    rows = render_rows([job], now=NOW)
    assert rows == ["● my-job · disabled · in 8h"]


def test_running():
    job = _job(state=m.JobState(next_run_at=NOW + 8 * 3600, running_since=NOW - 1000))
    rows = render_rows([job], now=NOW)
    assert rows == ["● my-job · running · in 8h"]


def test_running_beats_disabled():
    """running_since set takes priority even if enabled=False."""
    job = _job(enabled=False, state=m.JobState(running_since=NOW - 1000))
    rows = render_rows([job], now=NOW)
    assert rows[0].startswith("● my-job · running ·")


def test_multiple_jobs_order_preserved():
    j1 = _job(id="j1", name="alpha", state=m.JobState(next_run_at=NOW + 100.0))
    j2 = _job(id="j2", name="beta", state=m.JobState(next_run_at=NOW + 200.0))
    rows = render_rows([j1, j2], now=NOW)
    assert len(rows) == 2
    assert "alpha" in rows[0]
    assert "beta" in rows[1]
