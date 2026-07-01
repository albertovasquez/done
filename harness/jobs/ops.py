# harness/jobs/ops.py
import concurrent.futures
import time
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
    # A self-paced (Dynamic) turn returns its chosen reschedule delay via the
    # executor's return value; stays None on error/timeout → the loop pauses.
    override = None
    timeout_s = job.cost.timeout_s
    t0 = time.perf_counter()
    try:
        if timeout_s and timeout_s > 0:
            # Wall-clock budget around the synchronous executor. We run it on a
            # worker thread and stop WAITING at timeout_s; Python can't safely kill
            # the thread, so on timeout the underlying executor may finish in the
            # background (acceptable for v1) — we just record the timeout failure.
            # NOTE: do NOT use `with ThreadPoolExecutor(...)` — its __exit__ joins
            # the still-running worker (wait=True), which would re-block past the
            # timeout. We shut down WITHOUT waiting so the timeout is honored; the
            # daemon thread is left to exit on its own.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                fut = pool.submit(executor, job)
                override = fut.result(timeout=timeout_s)
            finally:
                pool.shutdown(wait=False)
        else:
            override = executor(job)     # timeout disabled (timeout_s <= 0) — run inline
        status = "ok"
    except OrphanPersona:
        raise                            # daemon.tick handles orphan — do NOT record a run
    except concurrent.futures.TimeoutError:
        status, error = "error", f"timeout after {timeout_s}s"
    except BaseException as e:           # mirror runner.py: process death = BaseException
        status, error = "error", str(e)
    elapsed = time.perf_counter() - t0
    run_rec = m.JobRun(job_id=job_id, started_at=now, duration=elapsed, status=status, error=error)
    store.append_run(run_rec, now=now)
    consec = 0 if status == "ok" else job.state.consecutive_errors + 1
    new_state = replace(job.state, last_run_at=now, last_status=status, last_error=error,
                        last_duration=elapsed,
                        consecutive_errors=consec,
                        next_run_at=m.next_run_at(
                            job.schedule, now, replace(job.state, last_run_at=now),
                            override=override, min_cadence_s=job.cost.min_cadence_s),
                        version=job.state.version + 1)
    disable = consec >= job.cost.max_consecutive_failures
    def fn(jobs):
        return [replace(j, state=new_state, enabled=(False if disable else j.enabled))
                if j.id == job_id else j for j in jobs]
    store.mutate(fn)
    return run_rec
