from dataclasses import replace

import pytest

from harness.jobs import model as m
from harness.jobs import ops
from harness.jobs.view import (
    JobRow,
    derive_status,
    format_elapsed,
    format_when_scheduled,
    job_rows,
)


@pytest.fixture(autouse=True)
def _isolated_jobs_store(tmp_path, monkeypatch):
    """Hermetic XDG_CONFIG_HOME so ops.add/list_jobs never touch the real store."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def _grant() -> m.Grant:
    return m.Grant(tools="*", paths="*", write=False, exec=False, network=False)


def _cost() -> m.CostGate:
    return m.CostGate(timeout_s=60, min_cadence_s=0, max_consecutive_failures=3)


def _job(*, name="Nightly sync", agent_id="fred", description="Syncs data",
          enabled=True, state=None, job_id=None) -> m.Job:
    return m.Job(
        id=job_id or name.lower().replace(" ", "-"),
        name=name,
        agent_id=agent_id,
        schedule=m.Every(seconds=3600),
        payload=m.AgentTurn(message="go"),
        grant=_grant(),
        cost=_cost(),
        state=state or m.JobState(),
        description=description,
        enabled=enabled,
    )


def _running_job(*, running_since: float) -> m.Job:
    return _job(state=m.JobState(running_since=running_since))


def _scheduled_job(*, next_run_at: float) -> m.Job:
    return _job(state=m.JobState(next_run_at=next_run_at))


def _disabled_job() -> m.Job:
    return _job(enabled=False)


def _done_job(*, last_status: str) -> m.Job:
    return _job(state=m.JobState(last_status=last_status))


def _queued_job(*, next_run_at: float) -> m.Job:
    return _job(state=m.JobState(next_run_at=next_run_at))


# ---- derive_status branches ----

def test_status_running():
    j = _running_job(running_since=100.0)
    assert derive_status(j, now=200.0) == "RUNNING"


def test_derive_status_branches():
    now = 1000.0
    assert derive_status(_running_job(running_since=900.0), now) == "RUNNING"
    assert derive_status(_scheduled_job(next_run_at=now + 7200), now) == "SCHEDULED"
    assert derive_status(_disabled_job(), now) == "DISABLED"
    assert derive_status(_done_job(last_status="ok"), now) == "COMPLETED"
    assert derive_status(_done_job(last_status="error"), now) == "FAILED"


def test_derive_status_queued_when_due():
    now = 1000.0
    assert derive_status(_queued_job(next_run_at=now - 1), now) == "QUEUED"
    assert derive_status(_queued_job(next_run_at=now), now) == "QUEUED"


def test_derive_status_fallback_scheduled():
    # enabled, no next_run_at, no last_status -> fallback SCHEDULED
    now = 1000.0
    assert derive_status(_job(state=m.JobState()), now) == "SCHEDULED"


def test_derive_status_running_beats_disabled():
    # running_since set even though job disabled -> RUNNING wins
    now = 1000.0
    j = _job(enabled=False, state=m.JobState(running_since=900.0))
    assert derive_status(j, now) == "RUNNING"


# ---- format helpers ----

def test_when_column_formats():
    assert format_when_scheduled(next_run_at=1000.0 + 2 * 86400 + 14 * 3600, now=1000.0) == "in 2d 14h"
    assert format_elapsed(running_since=1000.0 - 1122, now=1000.0) == "00:18:42"


def test_format_when_scheduled_hours_minutes_due():
    now = 1000.0
    assert format_when_scheduled(now + 3 * 3600 + 5 * 60, now) == "in 3h 5m"
    assert format_when_scheduled(now + 5 * 60, now) == "in 5m"
    assert format_when_scheduled(now, now) == "due"


# ---- job_rows / store integration ----

def test_progress_is_always_none_in_phase1():
    ops.add(_job(name="Nightly sync", agent_id="fred"), now=1000.0)
    rows = job_rows("fred", now=1000.0)
    assert rows
    assert all(r.progress is None for r in rows)


def test_job_rows_scoped_to_agent():
    ops.add(_job(name="Fred task", agent_id="fred"), now=1000.0)
    ops.add(_job(name="Sam task", agent_id="sam"), now=1000.0)
    rows = job_rows("fred", now=1000.0)
    assert all("sam" not in r.name.lower() for r in rows)
    assert any("fred" in r.name.lower() for r in rows)


def test_job_rows_maps_running_job_fields():
    ops.add(_job(name="Index repo", agent_id="fred", description="Scan graphs",
                  state=m.JobState(running_since=1000.0 - 1122)), now=1000.0)
    rows = job_rows("fred", now=1000.0)
    row = next(r for r in rows if r.name == "Index repo")
    assert row == JobRow(name="Index repo", description="Scan graphs", status="RUNNING",
                          progress=None, when="", elapsed="00:18:42")


def test_job_rows_empty_for_unknown_agent():
    assert job_rows("nobody", now=1000.0) == ()
