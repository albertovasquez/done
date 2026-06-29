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

def test_append_run_prunes_old_keeps_recent(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    # Run A: started_at=0.0, written at now=0.0
    store.append_run(m.JobRun(job_id="jX", started_at=0.0, duration=1.0, status="ok"), now=0.0)
    # Run B: started_at=31 days, written at now=31 days — A is older than 30 days relative to now
    t_b = 31 * 86400
    store.append_run(m.JobRun(job_id="jX", started_at=float(t_b), duration=1.0, status="ok"), now=float(t_b))
    lines = [json.loads(l) for l in jp.run_log("jX").read_text().splitlines() if l.strip()]
    assert len(lines) == 1, f"expected 1 line after prune, got {len(lines)}"
    assert lines[0]["started_at"] == float(t_b)

def test_append_run_skips_malformed_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    # Pre-seed the run log with a garbage line and a valid recent line
    recent_at = float(10 * 86400)
    valid_line = json.dumps({"job_id": "jX", "started_at": recent_at,
                             "duration": 0.5, "status": "ok", "error": None})
    jp.run_log("jX").parent.mkdir(parents=True, exist_ok=True)
    jp.run_log("jX").write_text("not json\n" + valid_line + "\n")
    # Append a new run at now=recent_at (so no prune by age)
    new_at = recent_at
    store.append_run(m.JobRun(job_id="jX", started_at=new_at, duration=2.0, status="error"), now=new_at)
    lines = [json.loads(l) for l in jp.run_log("jX").read_text().splitlines() if l.strip()]
    started_ats = [l["started_at"] for l in lines]
    assert recent_at in started_ats, "recent valid pre-existing line should survive"
    assert new_at in started_ats or len([l for l in lines if l["status"] == "error"]) == 1
    # Garbage line must be gone (only parseable JSON lines present)
    assert len(lines) == 2, f"expected 2 lines (valid pre-existing + new), got {len(lines)}"
