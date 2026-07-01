import pytest
from harness.jobs import ops, daemon, model as m


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def _seed():
    job = m.Job(
        id="loop1", name="loop", agent_id="a",
        schedule=m.Dynamic(), payload=m.AgentTurn(message="hi"),
        grant=m.Grant(tools=[], paths=[], write=False, exec=False, network=False),
        cost=m.CostGate(timeout_s=0, min_cadence_s=0, max_consecutive_failures=3),
        state=m.JobState(),
    )
    return ops.add(job, now=1000.0)


def test_dynamic_loop_fires_rearms_then_pauses():
    job = _seed()
    assert job.state.next_run_at == 1000.0            # fresh: armed at creation

    # Tick 1 at t=1000: due → fires. Executor "chooses" 50s.
    fired = daemon.tick(1000.0, executor=lambda j: 50)
    assert "loop1" in fired
    assert ops.get("loop1").state.next_run_at == 1050.0

    # Between fires it is NOT due.
    assert daemon.due_jobs(ops.list_jobs(include_disabled=False), now=1049.0) == []

    # Tick 2 at t=1050: due → fires. Executor returns None (work done → pause).
    daemon.tick(1050.0, executor=lambda j: None)
    assert ops.get("loop1").state.next_run_at is None

    # Paused: never due again.
    assert daemon.due_jobs(ops.list_jobs(include_disabled=False), now=9999.0) == []
