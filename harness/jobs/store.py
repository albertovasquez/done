# harness/jobs/store.py
import json, os, fcntl, time
from contextlib import contextmanager
from dataclasses import replace
from typing import Callable
from harness.jobs import paths as jp, model as m

def _ensure_dirs():
    jp.cron_dir().mkdir(parents=True, exist_ok=True)
    jp.runs_dir().mkdir(parents=True, exist_ok=True)

@contextmanager
def _locked():
    _ensure_dirs()
    lock = jp.cron_dir() / ".jobs.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

def _read_unlocked() -> list[m.Job]:
    f = jp.jobs_file()
    if not f.exists():
        return []
    doc = json.loads(f.read_text())
    return [m.job_from_dict(d) for d in doc.get("jobs", [])]

def _write_unlocked(jobs: list[m.Job]):
    doc = {"version": 1, "jobs": [m.job_to_dict(j) for j in jobs]}
    tmp = jp.jobs_file().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2))
    os.replace(tmp, jp.jobs_file())

def load() -> list[m.Job]:
    with _locked():
        return _read_unlocked()

def mutate(fn: Callable[[list[m.Job]], list[m.Job]]) -> None:
    with _locked():
        _write_unlocked(fn(_read_unlocked()))

def bump_state(job_id: str, new_state: m.JobState, expected_version: int) -> bool:
    with _locked():
        jobs = _read_unlocked()
        out, applied = [], False
        for j in jobs:
            if j.id == job_id:
                if j.state.version != expected_version:
                    return False
                out.append(replace(j, state=new_state)); applied = True
            else:
                out.append(j)
        if applied:
            _write_unlocked(out)
        return applied

def append_run(run: m.JobRun, *, now: float | None = None) -> None:
    _ensure_dirs()
    path = jp.run_log(run.job_id)
    cutoff = (now if now is not None else time.time()) - 30 * 86400
    kept = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                if json.loads(line).get("started_at", 0) >= cutoff:
                    kept.append(line)
            except json.JSONDecodeError:
                continue
    kept.append(json.dumps({"job_id": run.job_id, "started_at": run.started_at,
                            "duration": run.duration, "status": run.status, "error": run.error}))
    path.write_text("\n".join(kept) + "\n")
