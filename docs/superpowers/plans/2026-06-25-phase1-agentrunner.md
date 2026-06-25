# Phase 1 — AgentRunner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a client-facing `AgentRunner` that yields events live (a generator) and exposes a `RunResult`, bridging the Phase-0 `TracingAgent`'s pushed events to the generator via a background thread + thread-safe queue, with clients depending on the runner instead of `minisweagent`.

**Architecture:** A small `_EventSource` base in `events.py` owns seq/clock/Event-construction; `Emitter` (file/console) and a new `QueueEmitter` (in `runner.py`) both reuse it. `MiniSweAgentRunner` runs the unchanged `TracingAgent` on a background thread with a `QueueEmitter`; its `run()` generator pulls from the queue and yields, re-raising any worker exception after a `_DONE` sentinel. `run_traced.py` becomes a thin client that builds collaborators, iterates the runner, and persists each yielded event via `Emitter.write_event`.

**Tech Stack:** Python 3.11 (`.venv`), stdlib `threading` + `queue`, the vendored `minisweagent` (v2.4.2, editable-installed), `pytest`.

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/` changes. (spec §1)
- **Exactly two bounded, behavior-preserving changes to a reviewed Phase-0 file** (`events.py`): the `_EventSource` extraction and `Emitter.write_event`. All four existing `events.py` tests MUST pass unchanged. `tracing_agent.py` and `models_mock.py` are NOT modified. (spec §2)
- **Event model unchanged** from Phase 0: `Event(seq, t, type, data)`, same 6 types. (spec §1)
- **Single producer + synchronous enqueue.** Only the agent thread calls `QueueEmitter.emit`; `emit` does a synchronous `queue.put`. The same thread puts `_DONE` only after `agent.run()` returns/raises. This FIFO single-producer ordering is what guarantees `run.finished` is dequeued before `_DONE`. `seq`/clock are touched only on that one thread (no locks). (spec §2)
- **Worker catches `BaseException`** (not just `Exception`) — `TracingAgent` catches `BaseException`, so a worker-side `KeyboardInterrupt` must still reach the `_DONE` finally or the generator hangs forever. (spec §3)
- **Generator cleanup is blocking, with no cancellation.** Early `gen.close()`/`break` drains to `_DONE` and joins (waits for the agent to finish). An *abandoned* (never-closed) generator may outlive iteration — callers MUST exhaust or close. (spec §3)
- **RunResult provenance:** `exit_status`/`submission` from `TracingAgent.run()`'s returned dict (`messages[-1]["extra"]`); `ok`/`n_calls`/`total_cost`/`error` from the final `run.finished` event. On the error path the returned dict is absent → build from the event, `submission=""`. (spec §2)
- **Thin client uses `Emitter.write_event(event)`, NOT `emit()`** — re-`emit()`ing yielded events would reassign `seq`/`t` and corrupt the JSONL. (spec §4)
- **Python env:** run all tests as `.venv/bin/python -m pytest ...` (system python3 is 3.9, too old). Repo root: `/Users/alberto/Work/Quiubo/harness`.
- **Thin-client preservation (7 behaviors):** dotenv load, `output_path`/traj.json, the `except KeyboardInterrupt` branch, the VibeProxy error hint, `Emitter` close in `finally`, the events/trajectory path prints, and the `--model/--task/--cwd` CLI + exit 0. (spec §4)

---

## File Structure

| Path | Responsibility | Change |
|------|----------------|--------|
| `trace/events.py` | `Event`, `_EventSource` (seq/clock/`_next_event`), `Emitter` (+`write_event`) | MODIFY (bounded, behavior-preserving) |
| `trace/runner.py` | `RunResult`, `QueueEmitter`, `AgentRunner` (ABC), `MiniSweAgentRunner` | CREATE |
| `trace/run_traced.py` | thin client over the runner | REWIRE |
| `trace/tracing_agent.py`, `trace/models_mock.py` | unchanged | NONE |
| `tests/test_events.py` | existing 4 tests + new `write_event` test | MODIFY (add) |
| `tests/test_runner.py` | Tests 1,2,3,5,6 (runner behavior) | CREATE |
| `tests/test_run_traced.py` | Test 4 (thin-client integration) | CREATE |

---

### Task 1: Refactor `events.py` — `_EventSource` base + `Emitter.write_event`

**Files:**
- Modify: `trace/events.py`
- Test: `tests/test_events.py` (keep the 4 existing tests; add one for `write_event`)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `class _EventSource` with `__init__(self, clock)`, `_next_event(self, type, **data) -> Event` (assigns monotonic `seq` from 0, `t=round(clock(),3)`), and `set_clock(self, clock)`.
  - `Emitter(_EventSource)` keeps `__init__(jsonl_path, *, clock, console=True)`, `emit(type, **data) -> Event`, `_print_console`, `close()`, and ADDS `write_event(event: Event) -> None` (writes the GIVEN event to JSONL + console without reassigning seq/t). `emit` is refactored to `event = self._next_event(...); self.write_event(event); return event`.

- [ ] **Step 1: Add the failing test for `write_event`**

Append to `tests/test_events.py`:
```python
def test_write_event_persists_given_event_without_reassigning(tmp_path):
    p = tmp_path / "events.jsonl"
    em = Emitter(p, clock=lambda: 9.9, console=False)
    # An event built elsewhere (e.g. by a QueueEmitter) with its own seq/t:
    pre_built = Event(seq=42, t=1.23, type="action", data={"command": "ls"})
    em.write_event(pre_built)
    em.close()
    rec = json.loads(p.read_text().strip())
    assert rec["seq"] == 42      # NOT reassigned to the emitter's 0
    assert rec["t"] == 1.23      # NOT replaced by clock() == 9.9
    assert rec["type"] == "action"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_events.py::test_write_event_persists_given_event_without_reassigning -v`
Expected: FAIL with `AttributeError: 'Emitter' object has no attribute 'write_event'`.

- [ ] **Step 3: Refactor `events.py` to the shared base + `write_event`**

Replace the `Emitter` class (lines 30–67) — keep the `Event` dataclass and imports above it untouched — with:
```python
class _EventSource:
    """Owns the monotonic seq counter and the run clock; builds Events.
    Shared by Emitter (file/console) and QueueEmitter (runner)."""

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._seq = 0

    def _next_event(self, type: str, **data: Any) -> Event:
        event = Event(seq=self._seq, t=round(self._clock(), 3), type=type, data=data)
        self._seq += 1
        return event

    def set_clock(self, clock: Callable[[], float]) -> None:
        """Let the agent install its own run-relative clock at run start."""
        self._clock = clock


class Emitter(_EventSource):
    def __init__(self, jsonl_path: str | Path, *, clock: Callable[[], float], console: bool = True):
        super().__init__(clock)
        self._console = console
        # Loud failure at startup if the JSONL artifact cannot be created.
        self._fh = open(jsonl_path, "w", encoding="utf-8")

    def emit(self, type: str, **data: Any) -> Event:
        event = self._next_event(type, **data)
        self.write_event(event)
        return event

    def write_event(self, event: Event) -> None:
        """Persist an already-built event WITHOUT reassigning its seq/t.
        Used by emit() and by clients consuming events the runner already built."""
        # JSONL sink: best-effort per-line, but log on failure.
        try:
            self._fh.write(json.dumps(event.to_dict()) + "\n")
            self._fh.flush()
        except Exception:  # noqa: BLE001 — observation must not abort the observed
            logger.exception("failed to write event to JSONL")
        # Console sink: never crash the run.
        if self._console:
            try:
                self._print_console(event)
            except Exception:  # noqa: BLE001
                logger.exception("failed to print event to console")

    def _print_console(self, event: Event) -> None:
        parts = " ".join(f"{k}={v}" for k, v in event.data.items())
        print(f"[t={event.t:>5.1f}s] {event.type:<13} {parts}")

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001
            logger.exception("failed to close JSONL file")
```

- [ ] **Step 4: Run the full events test file (new test + the 4 existing pass unchanged)**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_events.py -v`
Expected: 5 passed (the 4 original + the new `write_event` test). The originals must pass WITHOUT edits — that proves the refactor is behavior-preserving.

- [ ] **Step 5: Run the whole suite (nothing else regressed)**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/ -q`
Expected: all pass (events 5 + models_mock 1 + tracing_agent 2 = 8).

- [ ] **Step 6: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness
git add trace/events.py tests/test_events.py
git -c user.name='harness' -c user.email='harness@local' commit -m "refactor(events): extract _EventSource base; add Emitter.write_event"
```

---

### Task 2: `runner.py` — RunResult, QueueEmitter, AgentRunner, MiniSweAgentRunner

**Files:**
- Create: `trace/runner.py`
- Test: `tests/test_runner.py` (Tests 1, 2, 3, 5, 6)

**Interfaces:**
- Consumes:
  - `trace.events._EventSource`, `trace.events.Event` (Task 1).
  - `trace.tracing_agent.TracingAgent` (unchanged Phase-0).
  - `minisweagent.environments.local.LocalEnvironment`, a model (mock or litellm).
- Produces:
  - `@dataclass RunResult(exit_status: str, ok: bool, n_calls: int, total_cost: float, submission: str = "", error: str | None = None)`.
  - `class QueueEmitter(_EventSource)`: `__init__(self, q: queue.Queue, *, clock)`, `emit(type, **data) -> Event` (builds via `_next_event`, `q.put(event)`, returns it), `close()` no-op.
  - `class AgentRunner(ABC)`: attribute `result: RunResult | None = None`; `@abstractmethod run(self, task, **kwargs) -> Iterator[Event]`.
  - `class MiniSweAgentRunner(AgentRunner)`: `__init__(self, model, env, *, agent_cfg: dict)`; `run(self, task, **kwargs) -> Iterator[Event]` generator that sets `self.result`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner.py`:
```python
import sys
import threading
import time

import pytest

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import yaml
from pathlib import Path
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from trace.runner import MiniSweAgentRunner, RunResult


def _agent_cfg() -> dict:
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _tc_model(turns):
    import json
    outputs = []
    for i, (content, commands) in enumerate(turns):
        tc_actions, tool_calls = [], []
        for j, command in enumerate(commands):
            tcid = f"call_{i}_{j}"
            tc_actions.append({"command": command, "tool_call_id": tcid})
            tool_calls.append({"id": tcid, "type": "function",
                               "function": {"name": "bash", "arguments": json.dumps({"command": command})}})
        outputs.append(make_toolcall_output(content, tool_calls, tc_actions))
    return DeterministicToolcallModel(outputs=outputs, cost_per_call=0.0)


def _raise_model(exc):
    # An action that raises when executed (test_models supports {"raise": exc}).
    out = make_toolcall_output("boom", [], [])
    out["extra"]["actions"] = [{"raise": exc}]
    out["extra"]["cost"] = 0.0
    return DeterministicToolcallModel(outputs=[out], cost_per_call=0.0)


def _runner(model, tmp_path):
    return MiniSweAgentRunner(model, LocalEnvironment(cwd=str(tmp_path)), agent_cfg=_agent_cfg())


def test_1_event_sequence_and_result(tmp_path):
    model = _tc_model([("hi", ["echo hi"]),
                       ("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])])
    runner = _runner(model, tmp_path)
    events = list(runner.run("t"))
    types = [e.type for e in events]
    assert types[0] == "run.started" and types[-1] == "run.finished"
    assert "llm.call" in types and "action.done" in types
    assert [e.seq for e in events] == list(range(len(events)))
    assert isinstance(runner.result, RunResult)
    assert runner.result.exit_status == "Submitted" and runner.result.ok is True
    # submission provenance: comes from the returned dict on the success path.
    # The submit sentinel produces an empty submission body, so "" is expected,
    # but the field must be a str sourced from the returned dict (not None).
    assert runner.result.submission == ""
    assert runner.result.error is None
    assert runner.result.n_calls >= 1


def test_2_terminal_submission_survives_bridge(tmp_path):
    model = _tc_model([("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])])
    runner = _runner(model, tmp_path)
    events = list(runner.run("t"))
    types = [e.type for e in events]
    submit_idx = next(i for i, e in enumerate(events)
                      if e.type == "action" and "COMPLETE_TASK" in e.data["command"])
    assert "action.done" in types[submit_idx + 1:]
    assert types[-1] == "run.finished" and runner.result.ok is True


def test_3_exception_propagation(tmp_path):
    runner = _runner(_raise_model(RuntimeError("kaboom")), tmp_path)
    seen = []
    with pytest.raises(RuntimeError, match="kaboom"):
        for e in runner.run("t"):
            seen.append(e.type)
    assert "run.finished" in seen  # terminal event flowed through before the raise


def test_5_baseexception_does_not_hang(tmp_path):
    runner = _runner(_raise_model(KeyboardInterrupt()), tmp_path)
    result_box = {}
    def drive():
        seen = []
        try:
            for e in runner.run("t"):
                seen.append(e.type)
        except BaseException as ex:  # noqa: BLE001
            result_box["exc"] = type(ex).__name__
            result_box["seen"] = seen
    th = threading.Thread(target=drive)
    th.start()
    th.join(timeout=10)
    assert not th.is_alive(), "generator hung on a worker-side BaseException"
    assert result_box.get("exc") == "KeyboardInterrupt"
    assert "run.finished" in result_box.get("seen", [])


def test_6_early_close_joins_worker(tmp_path):
    model = _tc_model([("hi", ["echo hi"]),
                       ("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])])
    runner = _runner(model, tmp_path)
    gen = runner.run("t")
    first = next(gen)
    assert first.type == "run.started"
    gen.close()  # must drain-to-_DONE and join the worker (blocking, mock finishes fast)
    # Give the worker a moment; assert no MiniSweAgentRunner worker thread is left alive.
    time.sleep(0.2)
    alive = [t for t in threading.enumerate() if t.name.startswith("agentrunner-")]
    assert alive == [], f"worker thread leaked after gen.close(): {alive}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trace.runner'`.

- [ ] **Step 3: Implement `runner.py`**

Create `trace/runner.py`:
```python
"""AgentRunner: client-facing interface that YIELDS events live.

Bridges the Phase-0 TracingAgent (which PUSHES events via emitter.emit deep in
its loop) to a generator (PULL) using a background thread + a thread-safe queue.

Threading contract (see spec §2/§3):
  - Exactly ONE producer (the agent thread) calls QueueEmitter.emit; emit does a
    synchronous queue.put. The same thread puts the _DONE sentinel AFTER
    agent.run() returns/raises. One FIFO queue + single producer => run.finished
    (emitted in TracingAgent.run()'s finally) is always dequeued before _DONE.
  - The worker catches BaseException (TracingAgent catches BaseException too), so
    a worker-side KeyboardInterrupt still reaches the _DONE finally; otherwise the
    generator would block forever on queue.get().
  - Generator cleanup (gen.close()/break) is BLOCKING: it drains to _DONE and
    joins the worker. No cooperative cancellation in Phase 1. An ABANDONED
    (never-closed) generator may outlive iteration — callers must exhaust or close.
"""

from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from trace.events import Event, _EventSource
from trace.tracing_agent import TracingAgent


@dataclass
class RunResult:
    exit_status: str
    ok: bool
    n_calls: int
    total_cost: float
    submission: str = ""
    error: str | None = None


class QueueEmitter(_EventSource):
    """Emitter that enqueues events instead of writing files. Sole producer is
    the agent thread. Satisfies the contract TracingAgent uses: set_clock + emit
    (inherited/defined here); close() is a no-op."""

    def __init__(self, q: "queue.Queue[Any]", *, clock: Callable[[], float]):
        super().__init__(clock)
        self._q = q

    def emit(self, type: str, **data: Any) -> Event:
        event = self._next_event(type, **data)
        self._q.put(event)  # synchronous; unbounded queue never blocks
        return event

    def close(self) -> None:  # no-op; the runner owns lifecycle
        pass


class _Done:
    """Sentinel put on the queue by the worker after agent.run() finishes.
    Carries the returned result dict (success) or the captured exception."""
    __slots__ = ("result_dict", "exc")

    def __init__(self, result_dict: dict | None, exc: BaseException | None):
        self.result_dict = result_dict
        self.exc = exc


class AgentRunner(ABC):
    result: RunResult | None = None

    @abstractmethod
    def run(self, task: str, **kwargs) -> Iterator[Event]: ...


class MiniSweAgentRunner(AgentRunner):
    def __init__(self, model, env, *, agent_cfg: dict):
        self._model = model
        self._env = env
        self._agent_cfg = agent_cfg
        self.result = None

    def run(self, task: str, **kwargs) -> Iterator[Event]:
        q: "queue.Queue[Any]" = queue.Queue()
        emitter = QueueEmitter(q, clock=lambda: 0.0)
        agent = TracingAgent(self._model, self._env, emitter=emitter, **self._agent_cfg)

        def _worker():
            result_dict = None
            exc: BaseException | None = None
            try:
                result_dict = agent.run(task, **kwargs)
            except BaseException as e:  # noqa: BLE001 — fidelity: capture & relay, incl. KeyboardInterrupt
                exc = e
            finally:
                q.put(_Done(result_dict, exc))

        thread = threading.Thread(target=_worker, name="agentrunner-worker", daemon=True)
        thread.start()

        last_finished: Event | None = None
        done: _Done | None = None
        try:
            while True:
                item = q.get()
                if isinstance(item, _Done):
                    done = item
                    break
                if item.type == "run.finished":
                    last_finished = item
                yield item
        finally:
            # Early close/break: drain to _DONE and join so the worker doesn't leak.
            if done is None:
                while True:
                    item = q.get()
                    if isinstance(item, _Done):
                        done = item
                        break
                    if item.type == "run.finished":
                        last_finished = item
            thread.join()

        # Assemble result, then re-raise on the caller's thread if the worker failed.
        self.result = _build_result(done, last_finished)
        if done.exc is not None:
            raise done.exc


def _build_result(done: "_Done", finished: Event | None) -> RunResult:
    fd = finished.data if finished else {}
    rd = done.result_dict or {}
    return RunResult(
        exit_status=rd.get("exit_status") or fd.get("exit_status", ""),
        ok=bool(fd.get("ok", done.exc is None)),
        n_calls=int(fd.get("n_calls", 0)),
        total_cost=float(fd.get("total_cost", 0.0)),
        submission=rd.get("submission", ""),  # "" on error path (rd absent)
        error=fd.get("exception_str") if done.exc is not None else None,
    )
```

- [ ] **Step 4: Run the runner tests**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_runner.py -v`
Expected: 5 passed (tests 1,2,3,5,6). If test 5 HANGS, the worker is catching `Exception` not `BaseException` — fix to `BaseException`.

- [ ] **Step 5: Run the whole suite**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/ -q`
Expected: all pass (events 5 + models_mock 1 + tracing_agent 2 + runner 5 = 13).

- [ ] **Step 6: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness
git add trace/runner.py tests/test_runner.py
git -c user.name='harness' -c user.email='harness@local' commit -m "feat(runner): AgentRunner + MiniSweAgentRunner with queue+thread bridge"
```

---

### Task 3: Rewire `run_traced.py` to a thin client + integration test

**Files:**
- Modify: `trace/run_traced.py`
- Test: `tests/test_run_traced.py` (Test 4)

**Interfaces:**
- Consumes: `trace.runner.MiniSweAgentRunner`, `trace.events.Emitter` (+`write_event`), the existing `_load_agent_config`/`_build_vibeproxy_model`/`build_mock_model`/`LocalEnvironment` wiring.
- Produces: same `main(argv) -> int` CLI; identical `./run.sh --model mock|vibeproxy` UX; writes `events.jsonl` (via `Emitter.write_event` on each yielded event) and `traj.json` (via the agent's `output_path`).

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_run_traced.py`:
```python
import json
import sys
from pathlib import Path

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import trace.run_traced as rt


def test_4_thin_client_mock_red_green(tmp_path, monkeypatch):
    # Copy the sample repo into a temp cwd so the run can edit it freely.
    src = Path("examples/sample-repo")
    dst = tmp_path / "sample-repo"
    dst.mkdir()
    for f in ("calculator.py", "test_calculator.py"):
        (dst / f).write_text((src / f).read_text())

    rc = rt.main(["--model", "mock", "--cwd", str(dst)])
    assert rc == 0

    # The mock fix was applied (genuine red->green preserved through the runner).
    assert "return a + b" in (dst / "calculator.py").read_text()

    # The latest events.jsonl parses and has contiguous seq (proves write_event,
    # not re-emit, was used: re-emit would renumber seq from 0 with the client clock).
    runs = sorted((Path("trace") / "runs").glob("*/events.jsonl"))
    rec = [json.loads(l) for l in runs[-1].read_text().splitlines()]
    assert [r["seq"] for r in rec] == list(range(len(rec)))
    assert rec[0]["type"] == "run.started" and rec[-1]["type"] == "run.finished"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_run_traced.py -v`
Expected: FAIL — `main()` still calls `agent.run()` directly (no runner), and the assertion on the runner-produced stream won't match the current direct-emit path (or the test imports a not-yet-rewired module). Confirm it fails before rewiring.

- [ ] **Step 3: Rewire `main()` in `run_traced.py`**

In `trace/run_traced.py`, replace the import of `TracingAgent` and the body of `main()` from the agent construction through the `finally` (currently lines ~29 and ~70–95) so the client builds a runner and consumes events via `write_event`. Keep `_load_agent_config`, `_build_vibeproxy_model`, `_run_id`, `DEFAULT_TASK`, the arg parsing, and `load_dotenv` exactly as they are.

Change the import block (line ~29) from:
```python
from trace.tracing_agent import TracingAgent  # noqa: E402
```
to:
```python
from trace.runner import MiniSweAgentRunner  # noqa: E402
```

Replace the model/env/agent construction + run + finally (the block starting `if args.model == "mock":` through `return 0`) with:
```python
    if args.model == "mock":
        model = build_mock_model()
    else:
        model = _build_vibeproxy_model()

    env = LocalEnvironment(cwd=args.cwd)
    agent_cfg = _load_agent_config()
    agent_cfg["output_path"] = str(run_dir / "traj.json")

    emitter = Emitter(run_dir / "events.jsonl", clock=lambda: 0.0, console=True)
    runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)

    try:
        for event in runner.run(args.task):
            emitter.write_event(event)   # persist+print WITHOUT reassigning seq/t
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        if args.model == "vibeproxy":
            print(f"\nVibeProxy run failed: {e}\n"
                  f"Is VibeProxy running on {os.getenv('VIBEPROXY_BASE_URL', 'http://localhost:8317/v1')}?",
                  file=sys.stderr)
        else:
            raise
    finally:
        emitter.close()
        print(f"\nevents:     {run_dir / 'events.jsonl'}")
        print(f"trajectory: {run_dir / 'traj.json'}")
    return 0
```
Note: the `Emitter` is now created AFTER `run_dir` exists (it already is) and BEFORE the loop. The old line that created `emitter` earlier (line ~68) and the old `agent = TracingAgent(...)` line are removed — there must be exactly one `Emitter(...)` construction, in the block above. The console clock stays `lambda: 0.0`; the runner's events already carry the agent's run-relative `t`, and `write_event` preserves it.

- [ ] **Step 4: Run the integration test**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_run_traced.py -v`
Expected: PASS.

- [ ] **Step 5: Run the live mock demo end-to-end (manual deliverable check)**

Run:
```bash
cd /Users/alberto/Work/Quiubo/harness
git checkout examples/sample-repo/calculator.py 2>/dev/null; git clean -fdq examples/sample-repo/ 2>/dev/null
./run.sh --model mock
git checkout examples/sample-repo/calculator.py 2>/dev/null; git clean -fdq examples/sample-repo/ 2>/dev/null
```
Expected: the live stream prints `run.started → … → run.finished ok=True exit_status=Submitted` (genuine red→green: an `action.done returncode=1` early, `returncode=0` after the fix), then the events/trajectory paths. Identical UX to Phase 0.

- [ ] **Step 6: Run the full suite**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/ -q`
Expected: all pass (events 5 + models_mock 1 + tracing_agent 2 + runner 5 + run_traced 1 = 14).

- [ ] **Step 7: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness
git add trace/run_traced.py tests/test_run_traced.py
git -c user.name='harness' -c user.email='harness@local' commit -m "refactor(cli): run_traced becomes a thin client over AgentRunner"
```

---

## Self-Review

**1. Spec coverage:**

- §1 criterion 1 (`AgentRunner` ABC, generator + `result`) → Task 2.
- §1 criterion 2 (`MiniSweAgentRunner` runs unchanged TracingAgent on a thread) → Task 2.
- §1 criterion 3 (thin client, same UX) → Task 3.
- §1 criterion 4 (event model unchanged) → Tasks 1–3 (no new types; `Event` untouched).
- §1 criterion 5 (tests: sequence, RunResult, Submitted survives, Exception + BaseException propagate without hanging, early-close joins, contiguous JSONL seq) → Tasks 2 (tests 1,2,3,5,6) + 3 (test 4).
- §2 `_EventSource` extraction + `write_event` (two bounded events.py changes) → Task 1.
- §2 RunResult provenance (returned dict vs run.finished; error-path submission="") → Task 2 `_build_result`.
- §2 single-producer/synchronous enqueue → Task 2 `QueueEmitter` + worker.
- §2 QueueEmitter satisfies set_clock/emit; close() no-op → Task 2.
- §3 worker catches BaseException → Task 2 `_worker`; guarded by Test 5.
- §3 generator cleanup blocking + join on early close → Task 2 generator `finally`; guarded by Test 6.
- §3 no producer deadlock (unbounded queue) → Task 2.
- §4 thin-client uses write_event not emit; 7 preserved behaviors → Task 3 (dotenv, output_path, KeyboardInterrupt branch, vibeproxy hint, emitter.close in finally, path prints, CLI/exit). Test 4 asserts contiguous seq (write_event proof) + red→green.

No gaps found.

**2. Placeholder scan:** No TBD/TODO/"add error handling"; every code step shows complete code.

**3. Type consistency:**
- `_EventSource._next_event(type, **data) -> Event` / `set_clock` — defined Task 1, used by `QueueEmitter` Task 2. ✓
- `Emitter.write_event(event: Event)` — defined Task 1, used by Task 3 client. ✓
- `MiniSweAgentRunner(model, env, *, agent_cfg)` + `run(task) -> Iterator[Event]` + `.result` — defined Task 2, used Task 3 + tests. ✓
- `RunResult(exit_status, ok, n_calls, total_cost, submission="", error=None)` — defined Task 2, asserted in tests 1/2. ✓
- `_Done(result_dict, exc)` sentinel + worker thread name `agentrunner-worker` — Task 2; Test 6 checks `agentrunner-` prefix (matches `name="agentrunner-worker"`). ✓
- Event types unchanged from Phase 0 — asserted in tests. ✓

One consistency fix applied during review: Test 6 asserts no thread whose name starts with `agentrunner-`, and the worker is created with `name="agentrunner-worker"` — prefixes match.
