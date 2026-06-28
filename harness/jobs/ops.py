# harness/jobs/ops.py
from dataclasses import replace
from harness.jobs import store, model as m
from harness.jobs.executor import OrphanPersona

def add(job: m.Job, *, now: float) -> m.Job:
    if not job.agent_id:
        raise ValueError("agent_id is required")
    nxt = m.next_run_at(job.schedule, now, job.state)
    job = replace(job, created_at=now, updated_at=now,
                  state=replace(job.state, next_run_at=nxt))
    store.mutate(lambda jobs: jobs + [job])
    return job

def list_jobs(include_disabled: bool = True, agent_id: str | None = None) -> list[m.Job]:
    out = store.load()
    if agent_id is not None:
        out = [j for j in out if j.agent_id == agent_id]
    if not include_disabled:
        out = [j for j in out if j.enabled]
    return out

def get(job_id: str) -> m.Job | None:
    return next((j for j in store.load() if j.id == job_id), None)

def update(job_id: str, *, now: float, **patch) -> m.Job:
    def fn(jobs):
        out = []
        for j in jobs:
            out.append(replace(j, updated_at=now, **patch) if j.id == job_id else j)
        return out
    store.mutate(fn)
    return get(job_id)

def remove(job_id: str) -> bool:
    before = {j.id for j in store.load()}
    store.mutate(lambda jobs: [j for j in jobs if j.id != job_id])
    return job_id in before

def run(job_id: str, *, executor, now: float, force: bool = False) -> m.JobRun:
    job = get(job_id)
    if job is None:
        raise KeyError(job_id)
    if not job.enabled and not force:
        run_rec = m.JobRun(job_id=job_id, started_at=now, duration=0.0, status="skipped", error=None)
        store.append_run(run_rec, now=now)
        return run_rec
    error = None
    try:
        executor(job)
        status = "ok"
    except OrphanPersona:
        raise                            # daemon.tick handles orphan — do NOT record a run
    except BaseException as e:           # mirror runner.py: process death = BaseException
        status, error = "error", str(e)
    run_rec = m.JobRun(job_id=job_id, started_at=now, duration=0.0, status=status, error=error)
    store.append_run(run_rec, now=now)
    consec = 0 if status == "ok" else job.state.consecutive_errors + 1
    new_state = replace(job.state, last_run_at=now, last_status=status, last_error=error,
                        consecutive_errors=consec,
                        next_run_at=m.next_run_at(job.schedule, now, replace(job.state, last_run_at=now)),
                        version=job.state.version + 1)
    disable = consec >= job.cost.max_consecutive_failures
    def fn(jobs):
        return [replace(j, state=new_state, enabled=(False if disable else j.enabled))
                if j.id == job_id else j for j in jobs]
    store.mutate(fn)
    return run_rec
