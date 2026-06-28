# tests/jobs/test_ops.py
import pytest
from harness.jobs import ops, model as m

def _job(**kw):
    base = dict(id="j1", name="n", agent_id="fred",
                schedule=m.Every(seconds=60), payload=m.Reminder(text="hi"),
                grant=m.Grant(tools="inherit", paths="workspace", write=False, exec=False, network=False),
                cost=m.CostGate(timeout_s=10, min_cadence_s=60, max_consecutive_failures=2),
                state=m.JobState())
    base.update(kw); return m.Job(**base)

@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)

def test_add_requires_agent_id():
    with pytest.raises(ValueError):
        ops.add(_job(agent_id=""), now=100.0)

def test_add_sets_next_run_and_lists():
    ops.add(_job(), now=100.0)
    j = ops.get("j1")
    assert j.state.next_run_at == 160.0
    assert [x.id for x in ops.list_jobs(agent_id="fred")] == ["j1"]

def test_run_records_and_autodisables_on_failures():
    ops.add(_job(), now=0.0)
    def boom(job): raise RuntimeError("nope")
    ops.run("j1", executor=boom, now=10.0)
    ops.run("j1", executor=boom, now=20.0)   # 2nd consecutive → auto-disable (threshold 2)
    j = ops.get("j1")
    assert j.state.consecutive_errors == 2
    assert j.enabled is False
    assert j.state.last_status == "error"

def test_run_resets_consecutive_errors_on_success():
    ops.add(_job(), now=0.0)
    def boom(job): raise RuntimeError("nope")
    ops.run("j1", executor=boom, now=10.0)   # first failure → consecutive_errors=1
    j = ops.get("j1")
    assert j.state.consecutive_errors == 1
    def ok(job): pass
    ops.run("j1", executor=ok, now=20.0)     # success → resets consecutive_errors
    j = ops.get("j1")
    assert j.state.consecutive_errors == 0
    assert j.state.last_status == "ok"

def test_run_skips_disabled_job_unless_forced():
    ops.add(_job(), now=0.0)
    ops.update("j1", now=1.0, enabled=False)

    called = []
    def record(job): called.append(job.id)

    # run without force → skipped, executor not called
    result = ops.run("j1", executor=record, now=10.0)
    assert result.status == "skipped"
    assert called == []
    assert ops.get("j1").enabled is False

    # run with force=True → executor IS called
    result = ops.run("j1", executor=record, now=20.0, force=True)
    assert result.status == "ok"
    assert called == ["j1"]
