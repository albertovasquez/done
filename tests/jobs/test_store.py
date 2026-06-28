# tests/jobs/test_store.py
import json
from harness.jobs import store, model as m
from harness.jobs import paths as jp

def _mk():
    return m.Job(id="j1", name="n", agent_id="fred",
                 schedule=m.Every(seconds=60), payload=m.Reminder(text="hi"),
                 grant=m.Grant(tools="inherit", paths="workspace", write=False, exec=False, network=False),
                 cost=m.CostGate(timeout_s=10, min_cadence_s=60, max_consecutive_failures=3),
                 state=m.JobState())

def test_mutate_persists(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    store.mutate(lambda jobs: jobs + [_mk()])
    assert [j.id for j in store.load()] == ["j1"]

def test_compare_and_swap_rejects_stale(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    store.mutate(lambda jobs: [_mk()])
    ok = store.bump_state("j1", m.JobState(last_status="ok", version=1), expected_version=0)
    assert ok is True
    stale = store.bump_state("j1", m.JobState(last_status="error", version=1), expected_version=0)
    assert stale is False  # version moved on; caller must re-read

def test_append_run_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    store.append_run(m.JobRun(job_id="j1", started_at=1.0, duration=2.0, status="ok"))
    lines = jp.run_log("j1").read_text().splitlines()
    assert json.loads(lines[0])["status"] == "ok"
