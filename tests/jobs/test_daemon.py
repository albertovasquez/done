# tests/jobs/test_daemon.py
"""Tests for harness/jobs/daemon.py — due_jobs, tick, run_forever."""
import asyncio
import pytest
from harness.jobs import ops, model as m
from harness.jobs.executor import OrphanPersona
from harness.jobs import daemon


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _job(**kw):
    base = dict(
        id="j1", name="n", agent_id="fred",
        schedule=m.Every(seconds=60),
        payload=m.Reminder(text="hi"),
        grant=m.Grant(tools="inherit", paths="workspace", write=False, exec=False, network=False),
        cost=m.CostGate(timeout_s=10, min_cadence_s=60, max_consecutive_failures=3),
        state=m.JobState(),
    )
    base.update(kw)
    return m.Job(**base)


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


# ---------------------------------------------------------------------------
# due_jobs
# ---------------------------------------------------------------------------

class TestDueJobs:
    def test_returns_job_when_due(self):
        job = _job(state=m.JobState(next_run_at=100.0))
        result = daemon.due_jobs([job], now=100.0)
        assert result == [job]

    def test_returns_job_when_overdue(self):
        job = _job(state=m.JobState(next_run_at=50.0))
        result = daemon.due_jobs([job], now=100.0)
        assert result == [job]

    def test_excludes_future_job(self):
        job = _job(state=m.JobState(next_run_at=200.0))
        result = daemon.due_jobs([job], now=100.0)
        assert result == []

    def test_excludes_disabled_job(self):
        job = _job(enabled=False, state=m.JobState(next_run_at=50.0))
        result = daemon.due_jobs([job], now=100.0)
        assert result == []

    def test_excludes_job_with_no_next_run_at(self):
        job = _job(state=m.JobState(next_run_at=None))
        result = daemon.due_jobs([job], now=100.0)
        assert result == []

    def test_mixed_jobs(self):
        due = _job(id="due", state=m.JobState(next_run_at=100.0))
        future = _job(id="future", state=m.JobState(next_run_at=200.0))
        disabled = _job(id="disabled", enabled=False, state=m.JobState(next_run_at=50.0))
        no_next = _job(id="no_next", state=m.JobState(next_run_at=None))
        result = daemon.due_jobs([due, future, disabled, no_next], now=100.0)
        assert [j.id for j in result] == ["due"]


# ---------------------------------------------------------------------------
# tick
# ---------------------------------------------------------------------------

class TestTick:
    def test_tick_runs_due_job_and_returns_id(self):
        ops.add(_job(), now=0.0)  # next_run_at = 60.0
        called = []
        def executor(job): called.append(job.id)
        ids = daemon.tick(70.0, executor=executor)
        assert ids == ["j1"]
        assert called == ["j1"]

    def test_tick_does_not_run_future_job(self):
        ops.add(_job(), now=0.0)  # next_run_at = 60.0
        called = []
        def executor(job): called.append(job.id)
        ids = daemon.tick(50.0, executor=executor)
        assert ids == []
        assert called == []

    def test_tick_disables_orphan_without_run_record(self):
        ops.add(_job(), now=0.0)  # next_run_at = 60.0

        def orphan_executor(job):
            raise OrphanPersona(job.agent_id)

        ids = daemon.tick(70.0, executor=orphan_executor)
        assert ids == ["j1"]  # still returned (processed)

        j = ops.get("j1")
        assert j.enabled is False
        # CRITICAL: no run record should have been stored
        assert j.state.last_status is None

    def test_tick_records_run_on_normal_error(self):
        ops.add(_job(), now=0.0)

        def boom(job): raise RuntimeError("normal failure")

        daemon.tick(70.0, executor=boom)
        j = ops.get("j1")
        assert j.state.last_status == "error"

    def test_tick_returns_all_processed_ids(self):
        ops.add(_job(id="j1"), now=0.0)
        ops.add(_job(id="j2"), now=0.0)

        called = []
        def executor(job): called.append(job.id)

        ids = daemon.tick(70.0, executor=executor)
        assert set(ids) == {"j1", "j2"}
        assert set(called) == {"j1", "j2"}

    def test_tick_uses_run_headless_turn_as_default_executor(self, monkeypatch):
        """tick(now) with no executor= uses run_headless_turn (resolved at call time)."""
        ops.add(_job(), now=0.0)
        called = []
        monkeypatch.setattr("harness.jobs.daemon.run_headless_turn", lambda job: called.append(job.id))
        ids = daemon.tick(70.0)  # no executor= kwarg
        assert ids == ["j1"]
        assert called == ["j1"]


# ---------------------------------------------------------------------------
# run_forever
# ---------------------------------------------------------------------------

class TestRunForever:
    def test_run_forever_calls_tick_each_iteration(self):
        ops.add(_job(), now=0.0)

        sleep_calls = []

        async def fake_sleep(interval):
            sleep_calls.append(interval)
            if len(sleep_calls) >= 3:
                raise asyncio.CancelledError

        times = iter([70.0, 130.0, 200.0])
        called = []
        def executor(job): called.append(job.id)

        async def go():
            with pytest.raises(asyncio.CancelledError):
                await daemon.run_forever(
                    interval=30.0,
                    clock=lambda: next(times),
                    sleep=fake_sleep,
                    executor=executor,
                )

        asyncio.run(go())

        assert len(sleep_calls) == 3
        assert sleep_calls[0] == 30.0

    def test_run_forever_passes_interval_to_sleep(self):
        ops.add(_job(), now=0.0)

        sleep_intervals = []
        call_count = 0

        async def fake_sleep(interval):
            nonlocal call_count
            sleep_intervals.append(interval)
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        def executor(job): pass

        times = iter([70.0, 130.0])

        async def go():
            with pytest.raises(asyncio.CancelledError):
                await daemon.run_forever(
                    interval=15.0,
                    clock=lambda: next(times),
                    sleep=fake_sleep,
                    executor=executor,
                )

        asyncio.run(go())

        assert all(i == 15.0 for i in sleep_intervals)

        assert all(i == 15.0 for i in sleep_intervals)

    def test_run_forever_survives_tick_error(self, monkeypatch):
        """A transient tick failure must be logged and the loop must continue to
        the next interval, not propagate and die."""
        tick_calls = []

        def flaky_tick(now, **kwargs):
            tick_calls.append(now)
            if len(tick_calls) == 1:
                raise RuntimeError("transient tick failure")
            return []

        monkeypatch.setattr("harness.jobs.daemon.tick", flaky_tick)

        sleep_calls = []

        async def fake_sleep(interval):
            sleep_calls.append(interval)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError   # stop the loop after the 2nd iteration

        times = iter([70.0, 130.0, 200.0])

        async def go():
            with pytest.raises(asyncio.CancelledError):
                await daemon.run_forever(
                    interval=30.0,
                    clock=lambda: next(times),
                    sleep=fake_sleep,
                    executor=lambda job: None,
                )

        asyncio.run(go())

        # The first tick raised, but the loop kept going: a 2nd tick ran and sleep
        # was awaited twice — the RuntimeError did NOT propagate.
        assert len(tick_calls) >= 2
        assert len(sleep_calls) >= 2
