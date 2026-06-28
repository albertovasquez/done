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
