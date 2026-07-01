import pytest
from harness.jobs import ops, model as m


# Canonical store-isolation fixture (copied from tests/jobs/test_ops.py): the
# store resolves its path via harness.paths.config_dir(), so redirecting that to
# tmp_path gives each test a private jobs file.
@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def _mk_dynamic_job(**cost_kw):
    cost = dict(timeout_s=0, min_cadence_s=0, max_consecutive_failures=3)
    cost.update(cost_kw)
    return m.Job(
        id="d1", name="loop", agent_id="a",
        schedule=m.Dynamic(), payload=m.AgentTurn(message="hi"),
        grant=m.Grant(tools=[], paths=[], write=False, exec=False, network=False),
        cost=m.CostGate(**cost), state=m.JobState(),
    )


def test_override_arms_next_run():
    ops.add(_mk_dynamic_job(), now=1000.0)   # fresh Dynamic → armed at now
    ops.run("d1", executor=lambda job: 300, now=2000.0)
    assert ops.get("d1").state.next_run_at == 2300.0


def test_override_floored_by_min_cadence():
    ops.add(_mk_dynamic_job(min_cadence_s=60), now=1000.0)
    ops.run("d1", executor=lambda job: 10, now=2000.0)
    assert ops.get("d1").state.next_run_at == 2060.0


def test_no_override_pauses():
    ops.add(_mk_dynamic_job(), now=1000.0)
    ops.run("d1", executor=lambda job: None, now=2000.0)
    assert ops.get("d1").state.next_run_at is None


def test_raising_turn_pauses_and_counts_error():
    ops.add(_mk_dynamic_job(), now=1000.0)
    def boom(job): raise RuntimeError("nope")
    ops.run("d1", executor=boom, now=2000.0)
    got = ops.get("d1")
    assert got.state.next_run_at is None
    assert got.state.consecutive_errors == 1
    assert got.state.last_status == "error"
