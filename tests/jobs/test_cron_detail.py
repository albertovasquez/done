# tests/jobs/test_cron_detail.py
"""Pure unit tests for read_run_series.

The CronDetail Textual widget is not exercised here — it requires a running
Textual app. These tests pin the pure reader contract.
"""
import json
import pytest
from harness.jobs import store, model as m
from harness.jobs import paths as jp
from harness.tui.widgets.cron_detail import read_run_series


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


# ── read_run_series contract ────────────────────────────────────────────────


def test_empty_when_file_absent():
    """Returns [] if the run log does not exist yet."""
    assert read_run_series("no-such-job") == []


def test_single_run():
    """Returns a single (started_at, duration, status) tuple."""
    now = 1_750_000_000.0   # recent epoch — within 30-day retention window
    store.append_run(m.JobRun(job_id="j1", started_at=now, duration=2.5, status="ok"), now=now)
    result = read_run_series("j1")
    assert result == [(now, 2.5, "ok")]


def test_multiple_runs_file_order():
    """Returns tuples in file (chronological) order.

    Use started_at values within the last 30-day retention window so that
    store.append_run does not prune them.
    """
    # Use a fixed recent epoch so all three are within the 30-day retention window.
    now = 1_750_000_000.0   # 2025-06 — recent enough to survive pruning
    t1, t2, t3 = now - 300.0, now - 200.0, now - 100.0
    store.append_run(m.JobRun(job_id="j2", started_at=t1, duration=1.0, status="ok"), now=now)
    store.append_run(m.JobRun(job_id="j2", started_at=t2, duration=3.0, status="error"), now=now)
    store.append_run(m.JobRun(job_id="j2", started_at=t3, duration=0.5, status="ok"), now=now)
    result = read_run_series("j2")
    assert len(result) == 3
    assert result[0] == (t1, 1.0, "ok")
    assert result[1] == (t2, 3.0, "error")
    assert result[2] == (t3, 0.5, "ok")


def test_skips_malformed_lines():
    """Non-JSON lines are silently skipped."""
    jp.run_log("j3").parent.mkdir(parents=True, exist_ok=True)
    jp.run_log("j3").write_text(
        "not json\n"
        + json.dumps({"job_id": "j3", "started_at": 5_000.0, "duration": 1.2, "status": "ok", "error": None})
        + "\n"
        + "also bad\n"
    )
    result = read_run_series("j3")
    assert result == [(5_000.0, 1.2, "ok")]
