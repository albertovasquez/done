# tests/jobs/test_cron_dashboard.py
"""Pure unit tests for cron_dashboard.render_rows.

The Textual widget itself (CronDashboard) is not exercised here — it requires a
running Textual app. These tests pin the display-string contract for the pure
render function.
"""
from harness.jobs import model as m
from harness.tui.widgets.cron_dashboard import render_rows


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


# ── render_rows contract ────────────────────────────────────────────────────

def test_empty_list():
    assert render_rows([]) == []


def test_scheduled_with_next_run():
    job = _job(state=m.JobState(next_run_at=1_700_000_000.0))
    rows = render_rows([job])
    assert rows == ["● my-job · scheduled · next @1700000000"]


def test_scheduled_no_next_run():
    job = _job(state=m.JobState(next_run_at=None))
    rows = render_rows([job])
    assert rows == ["● my-job · scheduled · next —"]


def test_disabled():
    job = _job(enabled=False, state=m.JobState(next_run_at=1_700_000_000.0))
    rows = render_rows([job])
    assert rows == ["● my-job · disabled · next @1700000000"]


def test_running():
    job = _job(state=m.JobState(next_run_at=1_700_000_000.0, running_since=1_699_999_000.0))
    rows = render_rows([job])
    assert rows == ["● my-job · running · next @1700000000"]


def test_running_beats_disabled():
    """running_since set takes priority even if enabled=False."""
    job = _job(enabled=False, state=m.JobState(running_since=1_699_999_000.0))
    rows = render_rows([job])
    assert rows[0].startswith("● my-job · running ·")


def test_multiple_jobs_order_preserved():
    j1 = _job(id="j1", name="alpha", state=m.JobState(next_run_at=100.0))
    j2 = _job(id="j2", name="beta", state=m.JobState(next_run_at=200.0))
    rows = render_rows([j1, j2])
    assert len(rows) == 2
    assert "alpha" in rows[0]
    assert "beta" in rows[1]


def test_next_run_truncates_float():
    """next_run_at is stored as float; displayed as int(@<epoch>)."""
    job = _job(state=m.JobState(next_run_at=1_700_000_000.9))
    rows = render_rows([job])
    assert "@1700000000" in rows[0]
