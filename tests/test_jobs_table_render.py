"""Unit tests for the pure render helpers in harness.tui.widgets.jobs_table.
Progress is always None from the real jobs backend in Phase 1 (view.py), so the
`_progress_cell` bar branch is dormant in production — covered here directly so
it doesn't rot uncovered."""
from harness.jobs.view import JobRow
from harness.tui.widgets.jobs_table import _chip, _progress_cell, render_table


def test_progress_cell_none_renders_dash_muted():
    out = _progress_cell(None)
    assert "—" in out
    assert "$muted" in out


def test_progress_cell_bar_renders_filled_and_empty_blocks():
    out = _progress_cell(0.64)
    assert "64%" in out
    assert "$accent" in out
    filled = int(round(0.64 * 20))
    assert "█" * filled in out
    assert "░" * (20 - filled) in out


def test_chip_maps_status_to_token():
    assert "$accent" in _chip("RUNNING")
    assert "$scheduled" in _chip("SCHEDULED")
    assert "$success" in _chip("COMPLETED")
    assert "$error" in _chip("FAILED")
    assert "$muted" in _chip("QUEUED")
    assert "$muted" in _chip("DISABLED")


def test_render_table_empty_state():
    out = render_table(())
    assert "No jobs for this agent" in out
    assert "$muted" in out


def test_render_table_progress_always_dash():
    rows = (JobRow("Task", "Desc", "RUNNING", None, "", "00:00:01"),)
    out = render_table(rows)
    assert "█" not in out and "░" not in out
    assert "—" in out
