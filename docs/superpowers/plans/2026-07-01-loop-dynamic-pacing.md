# Loop Dynamic Self-Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Dynamic` schedule kind to `done`'s jobs system so a scheduled turn can steer its own next-fire cadence (or pause the loop by not rescheduling), plus the `set_next_run` and `create_loop` agent tools that drive it.

**Architecture:** A dynamic loop is a `Job` with `schedule=Dynamic()`. Each run, the turn calls `set_next_run(delay_seconds)` which stamps `env._next_run_override`. `run_headless_turn` returns that override; `ops.run` feeds it to `m.next_run_at`, whose new `Dynamic` branch arms `now+max(override, min_cadence)` — or returns `None` (pause) when a already-run loop set no override. Reuses the entire daemon/executor/CostGate/Grant/store machinery unchanged.

**Tech Stack:** Python 3.11+, dataclasses (frozen tagged unions), pytest. No new dependencies.

## Global Constraints

- Python floor: `>=3.11` (project pyproject; `tomllib`/modern typing already assumed).
- Test command from worktree root: `.venv/bin/python -m pytest tests/ -q` (target `tests/` only).
- Frozen dataclasses in `model.py` — never mutate; use `dataclasses.replace`.
- `ops.run` is the SOLE writer of `next_run_at` — no other code path may write it.
- Tools return `{"output": str, "returncode": int, "exception_info": str | None}`.
- `agent_id` for any job comes from `env._active_persona`, NEVER from the model.
- Purely additive: the three existing schedule kinds (At/Every/Cron) and their serialization must be behavior-preserving.

---

### Task 1: `Dynamic` schedule kind + serialization + `next_run_at` branch

**Files:**
- Modify: `harness/jobs/model.py`
- Test: `tests/jobs/test_model_dynamic.py` (create)

**Interfaces:**
- Consumes: existing `Schedule` union, `JobState`, `next_run_at(schedule, now, state)`.
- Produces:
  - `Dynamic` dataclass (frozen, no fields).
  - `Dynamic` in the `Schedule = Union[...]`.
  - `schedule_to_dict(Dynamic()) == {"kind": "dynamic"}`; `schedule_from_dict({"kind":"dynamic"}) == Dynamic()`.
  - `next_run_at(schedule, now, state, *, override=None, min_cadence_s=0)` — NEW keyword-only params `override: int | None` and `min_cadence_s: int`, both defaulted so existing callers are unchanged. `Dynamic` branch:
    - fresh state (`state.last_run_at is None`) → `now`
    - `override` is not None → `now + max(override, min_cadence_s)`
    - else (ran, no override) → `None`

- [ ] **Step 1: Write the failing test**

Create `tests/jobs/test_model_dynamic.py`:

```python
from harness.jobs import model as m


def test_dynamic_roundtrip():
    d = m.Dynamic()
    assert m.schedule_to_dict(d) == {"kind": "dynamic"}
    assert m.schedule_from_dict({"kind": "dynamic"}) == d


def test_dynamic_fresh_state_arms_now():
    # Never run: arm immediately (first run on next tick).
    st = m.JobState()  # last_run_at is None
    assert m.next_run_at(m.Dynamic(), now=1000.0, state=st) == 1000.0


def test_dynamic_override_arms_now_plus_override():
    st = m.JobState(last_run_at=1000.0)
    got = m.next_run_at(m.Dynamic(), now=1000.0, state=st, override=300)
    assert got == 1300.0


def test_dynamic_override_floored_by_min_cadence():
    st = m.JobState(last_run_at=1000.0)
    got = m.next_run_at(m.Dynamic(), now=1000.0, state=st,
                        override=10, min_cadence_s=60)
    assert got == 1060.0  # 10 floored up to 60


def test_dynamic_no_override_after_run_pauses():
    st = m.JobState(last_run_at=1000.0)
    assert m.next_run_at(m.Dynamic(), now=1000.0, state=st) is None


def test_existing_every_unaffected_by_new_kwargs():
    st = m.JobState(last_run_at=1000.0)
    # override/min_cadence_s are ignored by Every.
    assert m.next_run_at(m.Every(seconds=50), now=1000.0, state=st,
                         override=999, min_cadence_s=999) == 1050.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_model_dynamic.py -q`
Expected: FAIL — `AttributeError: module 'harness.jobs.model' has no attribute 'Dynamic'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/jobs/model.py`:

Add the dataclass after the `Cron` definition (around line 15):

```python
@dataclass(frozen=True)
class Dynamic: pass   # self-paced: the turn stamps its own next-fire delay
```

Extend the union (line 16):

```python
Schedule = Union[At, Every, Cron, Dynamic]
```

In `schedule_to_dict` (before the final `return` for Cron), add:

```python
    if isinstance(s, Dynamic): return {"kind": "dynamic"}
```

In `schedule_from_dict` (before the `raise`), add:

```python
    if k == "dynamic": return Dynamic()
```

Replace the `next_run_at` signature and add the `Dynamic` branch:

```python
def next_run_at(schedule: "Schedule", now: float, state: "JobState",
                *, override: int | None = None, min_cadence_s: int = 0) -> float | None:
    if isinstance(schedule, At):
        if state.last_run_at is not None:
            return None
        return datetime.fromisoformat(schedule.when_iso).timestamp()
    if isinstance(schedule, Every):
        if state.last_run_at is None:
            base = schedule.anchor if schedule.anchor is not None else now
            return base + schedule.seconds
        return state.last_run_at + schedule.seconds
    if isinstance(schedule, Cron):
        tzinfo = ZoneInfo(schedule.tz) if schedule.tz else None
        base = datetime.fromtimestamp(state.last_run_at or now, tz=tzinfo)
        return croniter(schedule.expr, base).get_next(datetime).timestamp()
    if isinstance(schedule, Dynamic):
        if state.last_run_at is None:
            return now                      # fresh: arm on next tick
        if override is not None:
            return now + max(override, min_cadence_s)
        return None                          # ran, no reschedule → pause
    raise ValueError(f"unknown schedule {schedule!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/jobs/test_model_dynamic.py -q`
Expected: PASS (6 passed).

Also run the existing model tests to confirm no regression:
Run: `.venv/bin/python -m pytest tests/jobs/ -q -k "model or schedule or next_run"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/model.py tests/jobs/test_model_dynamic.py
git commit -m "feat(jobs): add Dynamic schedule kind with self-paced next_run_at

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `set_next_run` agent tool

**Files:**
- Create: `harness/tools/set_next_run.py`
- Test: `tests/tools/test_set_next_run.py` (create)

**Interfaces:**
- Consumes: the `Tool` protocol (`name`, `schema`, `display_label`, `execute`).
- Produces: `SetNextRunTool` with `name = "set_next_run"`. `execute(args, env)`:
  - validates `delay_seconds` is an int (or int-valued) and `> 0`; on failure returns `{"output": <msg>, "returncode": 1, "exception_info": None}` and stamps NOTHING.
  - on success sets `env._next_run_override = int(delay_seconds)` and returns `{"output": "Next run in <n>s.", "returncode": 0, "exception_info": None}`.
  - NEVER writes the job store.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_set_next_run.py`:

```python
import types
from harness.tools.set_next_run import SetNextRunTool


def _env():
    return types.SimpleNamespace()


def test_sets_override_on_env():
    env = _env()
    res = SetNextRunTool().execute({"delay_seconds": 300}, env)
    assert res["returncode"] == 0
    assert env._next_run_override == 300


def test_rejects_zero_and_negative():
    for bad in (0, -5):
        env = _env()
        res = SetNextRunTool().execute({"delay_seconds": bad}, env)
        assert res["returncode"] == 1
        assert not hasattr(env, "_next_run_override")


def test_rejects_non_int():
    env = _env()
    res = SetNextRunTool().execute({"delay_seconds": "soon"}, env)
    assert res["returncode"] == 1
    assert not hasattr(env, "_next_run_override")


def test_name_and_schema_shape():
    t = SetNextRunTool()
    assert t.name == "set_next_run"
    assert t.schema["function"]["name"] == "set_next_run"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/tools/test_set_next_run.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.tools.set_next_run'`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/tools/set_next_run.py`:

```python
"""SetNextRunTool: a dynamic-loop turn's way to steer its own cadence.

The turn calls set_next_run(delay_seconds=N) to schedule its next run N seconds
out. It stamps the intent onto the env (env._next_run_override) — it NEVER writes
the job store. ops.run reads the override off run_headless_turn's return value
after the turn ends and computes the new next_run_at (the sole store writer).

Omitting the call entirely pauses the loop: no override → next_run_at None (see
harness/jobs/model.py Dynamic branch)."""
from __future__ import annotations

SET_NEXT_RUN_TOOL = {
    "type": "function",
    "function": {
        "name": "set_next_run",
        "description": (
            "Schedule THIS self-paced loop's next run, `delay_seconds` from now. "
            "Call it once before you finish the turn to keep the loop going. "
            "Do NOT call it if the loop's work is done — omitting it pauses the "
            "loop. The delay is floored at the job's min-cadence."),
        "parameters": {
            "type": "object",
            "properties": {
                "delay_seconds": {
                    "type": "integer",
                    "description": "Seconds from now until the next run. Must be > 0.",
                },
            },
            "required": ["delay_seconds"],
        },
    },
}


class SetNextRunTool:
    name = "set_next_run"
    schema = SET_NEXT_RUN_TOOL

    def display_label(self, args: dict) -> str:
        return f"set_next_run {args.get('delay_seconds', '?')}s"

    def execute(self, args: dict, env) -> dict:
        raw = args.get("delay_seconds")
        # Accept ints and int-valued floats; reject bools, strings, None, <= 0.
        ok = isinstance(raw, int) and not isinstance(raw, bool)
        if not ok and isinstance(raw, float) and raw.is_integer():
            raw, ok = int(raw), True
        if not ok or raw <= 0:
            return {"output": f"delay_seconds must be a positive integer, got {raw!r}.",
                    "returncode": 1, "exception_info": None}
        env._next_run_override = int(raw)
        return {"output": f"Next run in {raw}s.", "returncode": 0, "exception_info": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/tools/test_set_next_run.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/tools/set_next_run.py tests/tools/test_set_next_run.py
git commit -m "feat(tools): add set_next_run tool for self-paced loop cadence

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Executor returns the override; register the tool for headless turns

**Files:**
- Modify: `harness/jobs/executor.py` (run_headless_turn return value + run_turn reads env override)
- Modify: `harness/tools/registry.py` (add SetNextRunTool to the always-present list)
- Test: `tests/jobs/test_executor_override.py` (create)

**Interfaces:**
- Consumes: `Deps.run_turn`, `env._next_run_override` (set by Task 2's tool).
- Produces:
  - `run_headless_turn(job, *, deps=None) -> int | None` — now RETURNS the override the turn stamped (or `None`). Reminder payloads and turns that never call set_next_run return `None`.
  - `SetNextRunTool` present in `build_registry(...)` output for headless turns.
- Consumed by Task 4 (`ops.run` reads this return value).

**Design note:** `Deps.run_turn` currently returns nothing and constructs the env internally (production path) — the override lives on `runner._env`. To surface it without changing every `Deps.run_turn` test double's signature, `run_turn` returns the override read off the env it built, and `run_headless_turn` returns whatever `deps.run_turn(...)` returns (defaulting to `None`). Test doubles that return `None` (the default) keep working — they just signal "no reschedule → pause," which is the correct fail-closed behavior for a double that doesn't model pacing.

- [ ] **Step 1: Write the failing test**

Create `tests/jobs/test_executor_override.py`:

```python
from pathlib import Path
from harness.jobs.executor import run_headless_turn, Deps
from harness.jobs.model import Job, JobState, Dynamic, AgentTurn, Grant, CostGate


def _job():
    return Job(
        id="j1", name="loop", agent_id="a",
        schedule=Dynamic(), payload=AgentTurn(message="hi"),
        grant=Grant(tools=[], paths=[], write=False, exec=False, network=False),
        cost=CostGate(timeout_s=0, min_cadence_s=0, max_consecutive_failures=3),
        state=JobState(),
    )


def _deps(run_turn):
    return Deps(
        resolve_workspace=lambda aid: Path("/tmp/ws"),
        resolve_model=lambda aid, **kw: "mock",
        compose=lambda ws: ("P", "M", ws),
        run_turn=run_turn,
    )


def test_run_headless_turn_returns_override():
    # A run_turn that "chose" 120s — return it, as the production run_turn does.
    deps = _deps(lambda **kw: 120)
    assert run_headless_turn(_job(), deps=deps) == 120


def test_run_headless_turn_none_when_no_reschedule():
    deps = _deps(lambda **kw: None)
    assert run_headless_turn(_job(), deps=deps) is None
```

Also add a registry test in the SAME file:

```python
def test_set_next_run_in_headless_registry():
    from harness.tools.registry import build_registry
    names = {t.name for t in build_registry()}
    assert "set_next_run" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_executor_override.py -q`
Expected: FAIL — `run_headless_turn` returns `None` for the override case (currently returns nothing), and `set_next_run` not in registry.

- [ ] **Step 3: Write minimal implementation**

In `harness/tools/registry.py`, add the import and include the tool in the always-present list:

```python
from harness.tools.set_next_run import SetNextRunTool
```

Change the `tools` list (line ~40) to include it:

```python
    tools: list[Tool] = [BashTool(), ReadTool(), WriteTool(), EditTool(), CreateJobTool(),
                         CreatePersonaTool(), SubagentTool(), ReviewTool(), SetNextRunTool()]
```

In `harness/jobs/executor.py`:

Make the production `run_turn` read and return the override. At the end of the inner `run_turn` (after the `for _ in runner.run(...): pass` loop, replacing the implicit `None` return):

```python
        # A self-paced (Dynamic) loop turn calls set_next_run, which stamps
        # env._next_run_override. Surface it so ops.run can arm the next run.
        return getattr(runner._env, "_next_run_override", None)
```

Make `run_headless_turn` return the override. Change the two return points:
- The Reminder branch `return` (line ~242) becomes `return None`.
- The final line changes from `deps.run_turn(**_turn_kwargs)` to `return deps.run_turn(**_turn_kwargs)`.

The function signature's return annotation changes to `-> int | None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/jobs/test_executor_override.py -q`
Expected: PASS (3 passed).

Run the full executor test file to confirm no regression:
Run: `.venv/bin/python -m pytest tests/jobs/ -q -k executor`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/executor.py harness/tools/registry.py tests/jobs/test_executor_override.py
git commit -m "feat(jobs): surface set_next_run override from headless turn + register tool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `ops.run` captures the override and arms the next run

**Files:**
- Modify: `harness/jobs/ops.py` (capture executor return, pass override + min_cadence to next_run_at)
- Test: `tests/jobs/test_ops_dynamic.py` (create)

**Interfaces:**
- Consumes: `run_headless_turn(job) -> int | None` (Task 3), `m.next_run_at(..., override=, min_cadence_s=)` (Task 1).
- Produces: after a `Dynamic` job runs, `ops.run` stores `next_run_at` computed from the captured override + `job.cost.min_cadence_s`. On no override / error / timeout → `next_run_at = None` (pause).

**Design note:** `ops.run` calls the executor in two places (timeout path via `fut.result()`, inline path directly). Capture the return in both into a local `override`. On any exception/timeout, `override` stays `None` (initialize it to `None` before the try). Then pass `override=override, min_cadence_s=job.cost.min_cadence_s` into the existing `m.next_run_at(...)` call that builds `new_state`.

- [ ] **Step 1: Write the failing test**

Create `tests/jobs/test_ops_dynamic.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_ops_dynamic.py -q`
Expected: FAIL — `next_run_at` is `None` even for the override case (ops.run ignores the executor return today).

- [ ] **Step 3: Write minimal implementation**

In `harness/jobs/ops.py`, in `run(...)`:

Initialize `override = None` before the `try`, capture the executor's return in both branches, and thread it into `next_run_at`:

```python
    error = None
    override = None
    timeout_s = job.cost.timeout_s
    t0 = time.perf_counter()
    try:
        if timeout_s and timeout_s > 0:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                fut = pool.submit(executor, job)
                override = fut.result(timeout=timeout_s)
            finally:
                pool.shutdown(wait=False)
        else:
            override = executor(job)      # timeout disabled — run inline
        status = "ok"
    except OrphanPersona:
        raise
    except concurrent.futures.TimeoutError:
        status, error = "error", f"timeout after {timeout_s}s"
    except BaseException as e:
        status, error = "error", str(e)
```

Then update the `new_state` construction's `next_run_at` call to pass the override and cadence floor:

```python
    new_state = replace(job.state, last_run_at=now, last_status=status, last_error=error,
                        last_duration=elapsed,
                        consecutive_errors=consec,
                        next_run_at=m.next_run_at(
                            job.schedule, now, replace(job.state, last_run_at=now),
                            override=override, min_cadence_s=job.cost.min_cadence_s),
                        version=job.state.version + 1)
```

(At/Every/Cron ignore the two new kwargs — behavior-preserving.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/jobs/test_ops_dynamic.py -q`
Expected: PASS (4 passed).

Run the full ops + daemon suite to confirm At/Every/Cron unaffected:
Run: `.venv/bin/python -m pytest tests/jobs/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/ops.py tests/jobs/test_ops_dynamic.py
git commit -m "feat(jobs): ops.run arms Dynamic next_run_at from turn override (pause on none)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `create_loop` agent tool

**Files:**
- Create: `harness/tools/create_loop.py`
- Modify: `harness/tools/registry.py` (register CreateLoopTool alongside CreateJobTool)
- Test: `tests/tools/test_create_loop.py` (create)

**Interfaces:**
- Consumes: `handle_create_job(spec, now=)` (create.py), `env._active_persona`, the `_normalize_cost` / `_normalize_grant` helpers.
- Produces: `CreateLoopTool` (`name = "create_loop"`). `execute(args, env)` builds a spec with `schedule={"kind":"dynamic"}` and `payload={"kind":"agent_turn","message": args["message"]}`, `agent_id` from `env._active_persona`, then calls `handle_create_job`. Returns the standard tool observation dict.

**DRY note:** import and reuse `_normalize_cost` and `_normalize_grant` from `harness.tools.create_job` — do NOT re-implement them. (They are module-level functions in create_job.py.)

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_create_loop.py`:

```python
import types
import pytest
from harness.tools.create_loop import CreateLoopTool


class _Recorder:
    def __init__(self): self.spec = None
    def __call__(self, spec, *, now):
        self.spec = spec
        return {"id": spec["id"], "name": spec["name"]}


def _env(persona="alice"):
    return types.SimpleNamespace(_active_persona=persona)


def test_builds_dynamic_agent_turn_bound_to_persona(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr("harness.tools.create_loop.handle_create_job", rec)
    res = CreateLoopTool().execute({
        "message": "check the deploy",
        "description": "deploy watcher",
        "cost": {"timeout_secs": 120, "min_cadence_secs": 60,
                 "max_consecutive_failures": 3},
        "grant": {"paths": [], "shell": False, "network": True},
    }, _env("alice"))
    assert res["returncode"] == 0
    assert rec.spec["agent_id"] == "alice"
    assert rec.spec["schedule"] == {"kind": "dynamic"}
    assert rec.spec["payload"] == {"kind": "agent_turn", "message": "check the deploy"}
    assert rec.spec["cost"]["timeout_s"] == 120        # normalized key
    assert rec.spec["cost"]["min_cadence_s"] == 60


def test_gate_failure_returns_returncode_1(monkeypatch):
    def boom(spec, *, now): raise ValueError("grant required (fail closed)")
    monkeypatch.setattr("harness.tools.create_loop.handle_create_job", boom)
    res = CreateLoopTool().execute({"message": "x", "cost": {}, "grant": {}},
                                   _env())
    assert res["returncode"] == 1
    assert "grant required" in res["output"]


def test_name_and_schema():
    t = CreateLoopTool()
    assert t.name == "create_loop"
    assert t.schema["function"]["name"] == "create_loop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/tools/test_create_loop.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.tools.create_loop'`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/tools/create_loop.py`:

```python
"""CreateLoopTool: create a self-paced (Dynamic) scheduled loop from chat.

Sibling of CreateJobTool. A loop is a Job whose schedule is Dynamic: each run
the turn calls set_next_run to steer its own cadence, or omits it to pause the
loop. The turn payload is always an AgentTurn (a loop runs the model, not a bare
reminder). agent_id comes from env._active_persona, never the model. Reuses the
same handle_create_job door + create-job gates (cost/grant fail-closed) and the
same cost/grant normalizers as create_job (DRY)."""
from __future__ import annotations

import time
import uuid

from harness.jobs.create import handle_create_job
from harness.tools.create_job import _normalize_cost, _normalize_grant

CREATE_LOOP_TOOL = {
    "type": "function",
    "function": {
        "name": "create_loop",
        "description": (
            "Create a SELF-PACED loop: a scheduled turn that decides its own "
            "cadence via set_next_run each run (omit it to pause the loop). Same "
            "four gates as create_job (timeout, min-cadence, max-failures, "
            "permissions). Do NOT pass agent_id — it is the active persona."),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description":
                            "The prompt this loop's turn runs each fire."},
                "description": {"type": "string",
                                "description": "What this loop does."},
                "cost": {
                    "type": "object",
                    "properties": {
                        "timeout_secs": {"type": "integer"},
                        "min_cadence_secs": {"type": "integer"},
                        "max_consecutive_failures": {"type": "integer"},
                    },
                    "required": ["timeout_secs", "min_cadence_secs",
                                 "max_consecutive_failures"],
                },
                "grant": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "shell": {"type": "boolean"},
                        "network": {"type": "boolean"},
                        "tools": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["paths", "shell", "network"],
                },
            },
            "required": ["message", "cost", "grant"],
        },
    },
}


class CreateLoopTool:
    name = "create_loop"
    schema = CREATE_LOOP_TOOL

    def display_label(self, args: dict) -> str:
        return f"create_loop {args.get('description', args.get('message', ''))[:40]}"

    def execute(self, args: dict, env) -> dict:
        agent_id = getattr(env, "_active_persona", None) or "default"
        description = args.get("description", "") or args.get("message", "")[:40]
        spec = {
            "id": uuid.uuid4().hex[:12],
            "name": (description[:40] or "loop"),
            "agent_id": agent_id,
            "description": description,
            "schedule": {"kind": "dynamic"},
            "cost": _normalize_cost(args.get("cost")) if args.get("cost") else args.get("cost"),
            "grant": _normalize_grant(args.get("grant")) if args.get("grant") else args.get("grant"),
            "payload": {"kind": "agent_turn", "message": args.get("message", "")},
        }
        try:
            result = handle_create_job(spec, now=time.time())
        except Exception as e:                       # fail-closed gate errors
            return {"output": f"Could not create loop: {e}", "returncode": 1,
                    "exception_info": None}
        return {"output": f"Created loop {result['id']} ({result['name']}) for "
                          f"persona '{agent_id}'.",
                "returncode": 0, "exception_info": None}
```

In `harness/tools/registry.py`, add the import and include it in the always-present list next to `CreateJobTool`:

```python
from harness.tools.create_loop import CreateLoopTool
```

```python
    tools: list[Tool] = [BashTool(), ReadTool(), WriteTool(), EditTool(), CreateJobTool(),
                         CreateLoopTool(), CreatePersonaTool(), SubagentTool(),
                         ReviewTool(), SetNextRunTool()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/tools/test_create_loop.py -q`
Expected: PASS (3 passed).

Confirm the registry now advertises both new tools:
Run: `.venv/bin/python -m pytest tests/jobs/test_executor_override.py -q -k registry`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tools/create_loop.py harness/tools/registry.py tests/tools/test_create_loop.py
git commit -m "feat(tools): add create_loop tool for self-paced Dynamic loops

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: End-to-end daemon integration test + full suite green

**Files:**
- Test: `tests/jobs/test_daemon_dynamic.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1–5 (`ops.add`, `daemon.tick`, `Dynamic`, `create_loop` path).
- Produces: proof that a Dynamic loop fires when due, re-arms on override, and pauses (drops out of `due_jobs`) after a no-override run.

- [ ] **Step 1: Write the failing test**

Create `tests/jobs/test_daemon_dynamic.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails (or passes if plumbing is complete)**

Run: `.venv/bin/python -m pytest tests/jobs/test_daemon_dynamic.py -q`
Expected: If Tasks 1–4 are correct this may already PASS. If it fails, the failure pinpoints the broken link (arming, override capture, or pause). Fix in the owning task, not here.

- [ ] **Step 3: (only if failing) Diagnose against the owning task**

No new production code should be needed — Task 6 is integration proof. If it fails, the bug is in Task 1 (`next_run_at`), Task 3 (executor return), or Task 4 (ops capture). Re-run that task's unit tests to localize, fix there.

- [ ] **Step 4: Run the FULL suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all green — new tests plus every pre-existing test).

- [ ] **Step 5: Commit**

```bash
git add tests/jobs/test_daemon_dynamic.py
git commit -m "test(jobs): end-to-end Dynamic loop fires, re-arms, and pauses via daemon

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Store isolation:** the store resolves its path via `harness.paths.config_dir()`. The canonical fixture (used verbatim in Tasks 4 and 6, copied from `tests/jobs/test_ops.py:15`) is `monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)` as an `autouse=True` fixture. Do NOT invent a `_STORE_PATH` knob — it does not exist.
- **No upstream edits.** All changes are inside `harness/` and `tests/`. Do not touch `upstream/` or the ACP engine.
- **Reminder payloads still work.** `run_headless_turn`'s Reminder branch returns `None` — a Reminder job that somehow had a Dynamic schedule would just pause after one run. Loops created via `create_loop` are always AgentTurn, so this is a non-issue in practice.
```
