# Phase 0 — Traced Fork of mini-swe-agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument a vendored, unmodified copy of mini-swe-agent with a live event tracer (console + JSONL) so the core agent loop's three seams — LLM call, shell exec, loop lifecycle — can be watched live, proven deterministically with a mock model, and optionally driven by VibeProxy.

**Architecture:** A `TracingAgent(DefaultAgent)` subclass overrides `run()`, `query()`, and `execute_actions()` to emit events through a tiny `Emitter` with two sinks. Upstream source in `upstream/` is never edited. A runner wires a mock or VibeProxy model + a `LocalEnvironment` + the tracing agent, loading config from upstream's own `mini.yaml`.

**Tech Stack:** Python 3, the vendored `minisweagent` package (v2.4.2), `litellm` (already a mini dependency), `python-dotenv` (already a mini dependency), `pytest`.

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/` may be modified. All new code lives in `trace/`, `examples/`, `docs/`, or the repo root. (spec §1, §2)
- **Upstream pinned to v2.4.2.** The vendored clone's nested `.git` is removed; version recorded in `upstream/UPSTREAM_VERSION`. (spec §1)
- **Mock run is the canonical deliverable.** It must be deterministic, zero-cost, and exercise the terminal submission seam. VibeProxy is a bonus, manually verified. (spec §1, §4)
- **No text-based fallback in Phase 0.** Only two model paths: `mock` (default) and `vibeproxy`. (spec §4)
- **VibeProxy contract:** `model_name="openai/<VIBEPROXY_MODEL>"`, `api_base` from `VIBEPROXY_BASE_URL` (default `http://localhost:8317/v1`), `api_key` from `VIBEPROXY_API_KEY` (default `dummy-not-used`), `cost_tracking="ignore_errors"` passed directly. (spec §4)
- **Explicit dotenv:** the runner must `load_dotenv()` on the repo-root `.env` itself; mini's own load targets the global config dir, not this repo. (spec §4)
- **`run.finished` is emitted in a `finally`** and reports `ok`/`exception_type`/`exception_str`. (spec §2, §3)
- **JSONL sink fails loudly at startup** if it can't open; console sink never crashes the run. (spec §5)
- Run the upstream commands from the **repo root** `/Users/alberto/Work/Quiubo/harness`.
- **Python environment (added during execution — system `python3` is 3.9.6, too old; mini-swe-agent requires >=3.10):** a virtualenv exists at `.venv` (Python 3.11.12) with the vendored package installed editable (`pip install -e ./upstream`) plus `pytest`. **All `python3 -m pytest ...` and `python3 ...` commands in the tasks below MUST be run as `.venv/bin/python -m pytest ...` / `.venv/bin/python ...`.** Because the install is editable, the `minisweagent` package imports without `PYTHONPATH`; the `PYTHONPATH=upstream/src` prefix in plan commands is harmless and may be dropped when using the venv. `.venv/` is gitignored.

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `upstream/` | Vendored mini-swe-agent v2.4.2, never edited. Already cloned. |
| `upstream/UPSTREAM_VERSION` | Records the pinned version + commit. |
| `trace/__init__.py` | Marks `trace` as a package. |
| `trace/events.py` | `Event` dataclass + `Emitter` (console + JSONL sinks). |
| `trace/tracing_agent.py` | `TracingAgent(DefaultAgent)` — the three overrides. |
| `trace/models_mock.py` | `build_mock_model()` returning a canned `DeterministicToolcallModel`. |
| `trace/run_traced.py` | Entrypoint: arg parse, dotenv, config, wire model+env+agent, run. |
| `examples/sample-repo/` | Tiny repo with one failing test for the agent to "fix". |
| `tests/test_tracing_agent.py` | Test A (happy path) + Test B (terminal submission). |
| `.env.example` | Committed config template. |
| `.gitignore` | Ignores `.env`, `trace/runs/`, `__pycache__`. |
| `docs/learning-log.md` | The learning deliverable, pre-seeded with seam prompts. |

---

### Task 1: Repo scaffolding + pinned upstream + import sanity

**Files:**
- Create: `upstream/UPSTREAM_VERSION`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `trace/__init__.py`
- Create: `run.sh` (convenience wrapper that sets `PYTHONPATH`)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: a `minisweagent` package importable when `PYTHONPATH=upstream/src`; a `trace` package; the repo-root `.env` convention.

- [ ] **Step 1: Record the pinned upstream version**

Run, from repo root:
```bash
cd /Users/alberto/Work/Quiubo/harness
python3 -c "import sys; sys.path.insert(0,'upstream/src'); import minisweagent; print(minisweagent.__version__)"
```
Expected: `2.4.2` (this prints a startup banner too — that is fine).

Then write `upstream/UPSTREAM_VERSION`:
```
mini-swe-agent 2.4.2
source: https://github.com/SWE-agent/mini-swe-agent
vendored 2026-06-25; nested .git removed; do not edit anything under upstream/
```

- [ ] **Step 2: Remove the nested upstream .git so the harness can be one clean repo**

Run:
```bash
rm -rf /Users/alberto/Work/Quiubo/harness/upstream/.git
```
Expected: no output; `ls -a upstream | grep .git` returns nothing.

- [ ] **Step 3: Create `.gitignore`**

```
.env
trace/runs/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 4: Create `.env.example`**

```
VIBEPROXY_BASE_URL=http://localhost:8317/v1
VIBEPROXY_MODEL=gpt-5.1-codex
VIBEPROXY_API_KEY=dummy-not-used
```

- [ ] **Step 5: Create `trace/__init__.py`**

```python
"""Phase-0 live tracer for the vendored mini-swe-agent. See docs/superpowers/specs."""
```

- [ ] **Step 6: Create `run.sh` convenience wrapper**

```bash
#!/usr/bin/env bash
# Run the traced agent with the vendored minisweagent on PYTHONPATH.
# Usage: ./run.sh [--model mock|vibeproxy] [--task "..."]
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="upstream/src:${PYTHONPATH:-}"
export MSWEA_SILENT_STARTUP=1   # suppress mini's startup banner
exec python3 trace/run_traced.py "$@"
```
Then: `chmod +x run.sh`

- [ ] **Step 7: Verify imports of the seam classes**

Run:
```bash
cd /Users/alberto/Work/Quiubo/harness
PYTHONPATH=upstream/src MSWEA_SILENT_STARTUP=1 python3 -c "
from minisweagent.agents.default import DefaultAgent, AgentConfig
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output
from minisweagent.exceptions import Submitted
print('imports OK')
"
```
Expected: `imports OK`

- [ ] **Step 8: Initialize git and commit**

```bash
cd /Users/alberto/Work/Quiubo/harness
git init
git add -A
git commit -m "chore: scaffold phase-0 traced fork; vendor mini-swe-agent 2.4.2"
```

---

### Task 2: Event model + Emitter

**Files:**
- Create: `trace/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `Event` dataclass: `Event(seq: int, t: float, type: str, data: dict)` with `.to_dict() -> dict`.
  - `Emitter(jsonl_path: str | Path, *, clock: Callable[[], float], console: bool = True)`.
    - `Emitter.emit(type: str, **data) -> Event` — assigns `seq` (monotonic from 0) and `t = clock()`, writes one JSON line, prints a console line, returns the `Event`.
    - `Emitter.close() -> None` — closes the JSONL file handle.
    - Constructor raises `OSError` immediately if `jsonl_path` cannot be opened (loud startup failure).
    - A console write failure inside `emit()` is swallowed (logged via `logging`), never raised.

- [ ] **Step 1: Write the failing test**

`tests/test_events.py`:
```python
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, "upstream/src")  # for any transitive import; events.py itself has none
sys.path.insert(0, ".")

from trace.events import Event, Emitter


def test_event_to_dict_roundtrips():
    e = Event(seq=3, t=1.5, type="llm.call", data={"n": 1})
    assert e.to_dict() == {"seq": 3, "t": 1.5, "type": "llm.call", "data": {"n": 1}}


def test_emitter_assigns_seq_and_writes_jsonl(tmp_path):
    p = tmp_path / "events.jsonl"
    ticks = iter([0.0, 0.1, 0.2])
    em = Emitter(p, clock=lambda: next(ticks), console=False)
    e0 = em.emit("run.started", task="x")
    e1 = em.emit("llm.call", n=1)
    em.close()

    assert (e0.seq, e1.seq) == (0, 1)
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["type"] == "run.started"
    assert rec0["seq"] == 0
    assert rec0["data"] == {"task": "x"}


def test_emitter_raises_loudly_when_path_unopenable(tmp_path):
    bad = tmp_path / "nonexistent_dir" / "events.jsonl"  # parent does not exist
    with pytest.raises(OSError):
        Emitter(bad, clock=lambda: 0.0, console=False)


def test_console_write_failure_does_not_crash(tmp_path, monkeypatch):
    p = tmp_path / "events.jsonl"
    em = Emitter(p, clock=lambda: 0.0, console=True)

    def boom(*a, **k):
        raise RuntimeError("console down")

    monkeypatch.setattr(em, "_print_console", boom)
    # Must not raise:
    em.emit("action", command="ls")
    em.close()
    assert len(p.read_text().strip().splitlines()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/alberto/Work/Quiubo/harness && python3 -m pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trace.events'` (or import error).

- [ ] **Step 3: Write minimal implementation**

`trace/events.py`:
```python
"""Event model and Emitter: the single point where a run becomes observable.

Two sinks share one Event:
  - JSONL file (durable; backs success criterion #2 — fails loudly if unopenable)
  - console (human-readable; best-effort, never crashes the run)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("trace.events")


@dataclass
class Event:
    seq: int
    t: float
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "t": self.t, "type": self.type, "data": self.data}


class Emitter:
    def __init__(self, jsonl_path: str | Path, *, clock: Callable[[], float], console: bool = True):
        self._clock = clock
        self._console = console
        self._seq = 0
        # Loud failure at startup if the JSONL artifact cannot be created.
        self._fh = open(jsonl_path, "w", encoding="utf-8")

    def emit(self, type: str, **data: Any) -> Event:
        event = Event(seq=self._seq, t=round(self._clock(), 3), type=type, data=data)
        self._seq += 1
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
        return event

    def _print_console(self, event: Event) -> None:
        parts = " ".join(f"{k}={v}" for k, v in event.data.items())
        print(f"[t={event.t:>5.1f}s] {event.type:<13} {parts}")

    def set_clock(self, clock: Callable[[], float]) -> None:
        """Let the agent install its own run-relative clock at run start."""
        self._clock = clock

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001
            logger.exception("failed to close JSONL file")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/alberto/Work/Quiubo/harness && python3 -m pytest tests/test_events.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add trace/events.py tests/test_events.py
git commit -m "feat(trace): event model + emitter with dual sinks"
```

---

### Task 3: Sample repo with one failing test

**Files:**
- Create: `examples/sample-repo/calculator.py`
- Create: `examples/sample-repo/test_calculator.py`
- Create: `examples/sample-repo/README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a directory the agent runs in (`cwd`); `test_calculator.py` fails before the fix, passes after a one-line change to `calculator.py`. The mock model (Task 4) is scripted to make exactly that change.

- [ ] **Step 1: Create the buggy module**

`examples/sample-repo/calculator.py`:
```python
def add(a, b):
    # BUG: subtracts instead of adds
    return a - b
```

- [ ] **Step 2: Create the failing test**

`examples/sample-repo/test_calculator.py`:
```python
from calculator import add


def test_add():
    assert add(2, 3) == 5
```

- [ ] **Step 3: Create a short README**

`examples/sample-repo/README.md`:
```markdown
# sample-repo

A deliberately tiny repo with one failing test (`add` subtracts instead of
adds). Used by the Phase-0 tracer demo: the mock model "fixes" `calculator.py`
so `test_calculator.py` passes.
```

- [ ] **Step 4: Verify the test fails as expected**

Run:
```bash
cd /Users/alberto/Work/Quiubo/harness/examples/sample-repo && python3 -m pytest test_calculator.py -q; echo "exit=$?"
```
Expected: 1 failed; `exit=1`.

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness
git add examples/sample-repo
git commit -m "test(examples): tiny sample repo with one failing test"
```

---

### Task 4: Mock model (deterministic, zero-cost, ends in submission)

**Files:**
- Create: `trace/models_mock.py`
- Test: `tests/test_models_mock.py`

**Interfaces:**
- Consumes: `minisweagent.models.test_models.{DeterministicToolcallModel, make_toolcall_output}`.
- Produces: `build_mock_model() -> DeterministicToolcallModel`. Its `outputs` are a fixed sequence of tool-call turns whose commands, when run in `examples/sample-repo`, fix the bug and then submit. `cost_per_call=0.0`. The final turn's command is `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` (drives `Submitted`).

- [ ] **Step 1: Write the failing test**

`tests/test_models_mock.py`:
```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from trace.models_mock import build_mock_model


def test_mock_model_sequence_shape():
    model = build_mock_model()
    assert model.config.cost_per_call == 0.0
    outputs = model.config.outputs
    # Each output is a tool-call assistant turn with at least one action.
    for out in outputs:
        assert out["role"] == "assistant"
        actions = out["extra"]["actions"]
        assert actions, "every mock turn must carry an action"
        for a, tc in zip(actions, out["tool_calls"]):
            assert a["tool_call_id"] == tc["id"]  # ids must match for observation pairing

    # Final command must be the submit sentinel.
    last_cmd = outputs[-1]["extra"]["actions"][-1]["command"]
    assert last_cmd == "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/alberto/Work/Quiubo/harness && python3 -m pytest tests/test_models_mock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trace.models_mock'`.

- [ ] **Step 3: Write minimal implementation**

`trace/models_mock.py`. The `_make_tc_model` helper mirrors upstream's own
`tests/agents/test_default.py::make_tc_model` pattern exactly (tool_call `id`,
`type: function`, `function.arguments` JSON; matching action `tool_call_id`).

```python
"""A canned tool-call model: deterministic, zero-cost, ends in submission.

The command sequence, run in examples/sample-repo, fixes the `add` bug then
submits. Mirrors upstream tests/agents/test_default.py::make_tc_model so the
tool-call/observation pairing is exactly the shape the real LitellmModel emits.
"""

from __future__ import annotations

from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

# (assistant_text, [commands]) per turn.
_TURNS: list[tuple[str, list[str]]] = [
    ("Let me reproduce the failure first.",
     ["cd examples/sample-repo && python3 -m pytest test_calculator.py -q || true"]),
    ("The add() function subtracts. I'll fix it.",
     ["cd examples/sample-repo && sed -i '' 's/return a - b/return a + b/' calculator.py"]),
    ("Re-running the test to confirm the fix.",
     ["cd examples/sample-repo && python3 -m pytest test_calculator.py -q"]),
    ("Test passes. Submitting.",
     ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]),
]


def _make_tc_model(turns: list[tuple[str, list[str]]]) -> DeterministicToolcallModel:
    outputs = []
    for i, (content, commands) in enumerate(turns):
        tc_actions, tool_calls = [], []
        for j, command in enumerate(commands):
            tcid = f"call_{i}_{j}"
            tc_actions.append({"command": command, "tool_call_id": tcid})
            tool_calls.append({
                "id": tcid,
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command": ' + _json_str(command) + "}"},
            })
        outputs.append(make_toolcall_output(content, tool_calls, tc_actions))
    return DeterministicToolcallModel(outputs=outputs, cost_per_call=0.0)


def _json_str(s: str) -> str:
    import json
    return json.dumps(s)


def build_mock_model() -> DeterministicToolcallModel:
    return _make_tc_model(_TURNS)
```

> Note on the `sed` command: `sed -i ''` is the BSD/macOS in-place form (the
> platform for this repo, per the environment). On Linux use `sed -i`. If
> portability matters later, switch to a Python one-liner; for Phase 0 macOS is
> the target.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/alberto/Work/Quiubo/harness && python3 -m pytest tests/test_models_mock.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trace/models_mock.py tests/test_models_mock.py
git commit -m "feat(trace): deterministic zero-cost mock model ending in submission"
```

---

### Task 5: TracingAgent — the three overrides

**Files:**
- Create: `trace/tracing_agent.py`
- Test: `tests/test_tracing_agent.py` (Test A + Test B)

**Interfaces:**
- Consumes:
  - `minisweagent.agents.default.DefaultAgent` (overrides `run`, `query`, `execute_actions`).
  - `minisweagent.exceptions.{Submitted, LimitsExceeded, TimeExceeded, InterruptAgentFlow}`.
  - `trace.events.Emitter` (from Task 2): `emitter.emit(type, **data)`.
- Produces:
  - `class TracingAgent(DefaultAgent)` with `__init__(self, model, env, *, emitter: Emitter, **kwargs)` forwarding `model, env, **kwargs` to `super().__init__`.
  - Emits the event sequence specified in spec §3: `run.started`, `llm.call`, `llm.return`, `action`, `action.done`, `run.finished` (in a `finally`, with `ok`/`exception_type`/`exception_str`).

- [ ] **Step 1: Write the failing tests (A: happy path, B: terminal submission)**

`tests/test_tracing_agent.py`:
```python
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import yaml
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from trace.events import Emitter
from trace.tracing_agent import TracingAgent


def _agent_config() -> dict:
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _tc_model(turns):
    outputs = []
    for i, (content, commands) in enumerate(turns):
        tc_actions, tool_calls = [], []
        for j, command in enumerate(commands):
            tcid = f"call_{i}_{j}"
            tc_actions.append({"command": command, "tool_call_id": tcid})
            tool_calls.append({
                "id": tcid, "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": command})},
            })
        outputs.append(make_toolcall_output(content, tool_calls, tc_actions))
    return DeterministicToolcallModel(outputs=outputs, cost_per_call=0.0)


def _run(tmp_path, turns, cwd):
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    model = _tc_model(turns)
    env = LocalEnvironment(cwd=str(cwd))
    agent_cfg = _agent_config()
    agent_cfg["output_path"] = str(tmp_path / "traj.json")
    agent = TracingAgent(model, env, emitter=emitter, **agent_cfg)
    agent.run("dummy task")
    emitter.close()
    records = [json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    return records


def test_A_happy_path_sequence(tmp_path):
    turns = [
        ("hello", ["echo hi"]),
        ("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]),
    ]
    records = _run(tmp_path, turns, cwd=tmp_path)
    types = [r["type"] for r in records]
    assert types[0] == "run.started"
    assert types[-1] == "run.finished"
    assert "llm.call" in types and "llm.return" in types
    assert "action" in types and "action.done" in types
    assert records[-1]["data"]["ok"] is True
    # seq is strictly increasing from 0
    assert [r["seq"] for r in records] == list(range(len(records)))


def test_B_terminal_submission_emits_action_done(tmp_path):
    # The FINAL action is the submit sentinel, which makes env.execute raise
    # Submitted BEFORE returning. action.done for that action must still appear.
    turns = [("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])]
    records = _run(tmp_path, turns, cwd=tmp_path)

    # Find the submit action and assert a following action.done exists.
    submit_idx = next(i for i, r in enumerate(records)
                      if r["type"] == "action"
                      and "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in r["data"]["command"])
    later_types = [r["type"] for r in records[submit_idx + 1:]]
    assert "action.done" in later_types, "final action.done was dropped on Submitted"
    assert records[-1]["type"] == "run.finished"
    assert records[-1]["data"]["ok"] is True
    assert records[-1]["data"]["exit_status"] == "Submitted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alberto/Work/Quiubo/harness && python3 -m pytest tests/test_tracing_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trace.tracing_agent'`.

- [ ] **Step 3: Write the implementation**

`trace/tracing_agent.py`. The override bodies re-express the parent's logic
exactly where a pure wrapper cannot place the event (per spec §2):

```python
"""TracingAgent: subclass of DefaultAgent that emits live events at the three
seams without editing upstream. See docs/superpowers/specs/2026-06-24-... §2.

Why reimplement instead of pure-wrap:
  - query():  parent does limit checks BEFORE the model call; a pre-super()
              llm.call would fire falsely on LimitsExceeded/TimeExceeded.
  - execute_actions(): parent's body is a list-comp over env.execute, and the
              submit command raises Submitted BEFORE returning, so a post-wrap
              never emits action.done for the final action.
  - run():    parent re-raises on uncaught exceptions, so run.finished must be
              emitted in a finally.
The duplicated lines are pinned to upstream v2.4.2 (see UPSTREAM_VERSION).
"""

from __future__ import annotations

import time
import traceback

from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import LimitsExceeded, Submitted, TimeExceeded

from trace.events import Emitter


class TracingAgent(DefaultAgent):
    def __init__(self, model, env, *, emitter: Emitter, **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._run_start = time.time()  # tracer-local clock; parent's _start_time is set in __init__

    def _t(self) -> float:
        return time.time() - self._run_start

    # --- seam 1: loop lifecycle ---
    def run(self, task: str = "", **kwargs) -> dict:
        self._run_start = time.time()
        self._emitter.set_clock(self._t)  # emitter timestamps relative to this run
        self._emitter.emit("run.started", task=task,
                           model_name=getattr(self.model.config, "model_name", "unknown"),
                           cwd=getattr(self.env.config, "cwd", ""))
        exc_type = exc_str = None
        result: dict = {}
        try:
            result = super().run(task, **kwargs)
            return result
        except Exception as e:  # noqa: BLE001 — record then re-raise
            exc_type, exc_str = type(e).__name__, str(e)
            raise
        finally:
            last_extra = self.messages[-1].get("extra", {}) if self.messages else {}
            self._emitter.emit(
                "run.finished",
                ok=exc_type is None,
                exit_status=last_extra.get("exit_status", "") or (exc_type or ""),
                n_calls=self.n_calls,
                total_cost=round(self.cost, 6),
                elapsed_s=round(self._t(), 3),
                exception_type=exc_type,
                exception_str=exc_str,
            )

    # --- seam 2: LLM call ---
    def query(self) -> dict:
        # Reproduce parent limit checks first (default.py:128-139) so llm.call is honest.
        if 0 < self.config.step_limit <= self.n_calls or 0 < self.config.cost_limit <= self.cost:
            raise LimitsExceeded({"role": "exit", "content": "LimitsExceeded",
                                  "extra": {"exit_status": "LimitsExceeded", "submission": ""}})
        if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):
            raise TimeExceeded({"role": "exit", "content": "TimeExceeded",
                                "extra": {"exit_status": "TimeExceeded", "submission": ""}})
        self._emitter.emit("llm.call", n=self.n_calls + 1, n_messages=len(self.messages))
        self.n_calls += 1
        message = self.model.query(self.messages)
        self.cost += message.get("extra", {}).get("cost", 0.0)
        self.add_messages(message)
        extra = message.get("extra", {})
        content = message.get("content") or ""
        preview = content[:120] if isinstance(content, str) else str(content)[:120]
        self._emitter.emit("llm.return", n=self.n_calls,
                           cost=round(extra.get("cost", 0.0), 6),
                           n_actions=len(extra.get("actions", [])),
                           content_preview=preview)
        return message

    # --- seam 3: shell exec ---
    def execute_actions(self, message: dict) -> list[dict]:
        outputs = []
        for action in message.get("extra", {}).get("actions", []):
            command = action.get("command", "")
            self._emitter.emit("action", command=command)
            try:
                output = self.env.execute(action)
            except Submitted:
                # The submit command finished successfully; env raised before
                # returning. Emit the done event, then re-raise so the loop ends.
                self._emitter.emit("action.done", returncode=0, output_bytes=0)
                raise
            outputs.append(output)
            self._emitter.emit("action.done",
                               returncode=output.get("returncode", -1),
                               output_bytes=len(str(output.get("output", "")).encode("utf-8")))
        return self.add_messages(
            *self.model.format_observation_messages(message, outputs, self.get_template_vars())
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/alberto/Work/Quiubo/harness && python3 -m pytest tests/test_tracing_agent.py -v`
Expected: PASS (2 passed). If Test B fails with "final action.done was dropped", the `except Submitted` branch is the regression it guards.

- [ ] **Step 5: Run the full test suite**

Run: `cd /Users/alberto/Work/Quiubo/harness && python3 -m pytest tests/ -v`
Expected: all pass (events + models_mock + tracing_agent).

- [ ] **Step 6: Commit**

```bash
git add trace/tracing_agent.py tests/test_tracing_agent.py
git commit -m "feat(trace): TracingAgent emits events at all three seams"
```

---

### Task 6: Runner — wire mock & VibeProxy, dotenv, config

**Files:**
- Create: `trace/run_traced.py`

**Interfaces:**
- Consumes:
  - `trace.events.Emitter`, `trace.tracing_agent.TracingAgent`, `trace.models_mock.build_mock_model`.
  - `minisweagent.environments.local.LocalEnvironment`.
  - `minisweagent.models.litellm_model.LitellmModel` (vibeproxy path).
  - upstream config via `yaml.safe_load("upstream/src/minisweagent/config/mini.yaml")["agent"]`.
- Produces: a CLI entrypoint. `python3 trace/run_traced.py [--model mock|vibeproxy] [--task TEXT]`. Writes `trace/runs/<runid>/events.jsonl` and `trace/runs/<runid>/traj.json`.

- [ ] **Step 1: Write the runner**

`trace/run_traced.py`:
```python
#!/usr/bin/env python3
"""Phase-0 entrypoint: run the vendored agent under the live tracer.

  python3 trace/run_traced.py                 # mock (default), zero cost
  python3 trace/run_traced.py --model vibeproxy --task "fix the add bug"

Run via ./run.sh so PYTHONPATH includes upstream/src.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "upstream" / "src"))
sys.path.insert(0, str(REPO_ROOT))

from minisweagent.environments.local import LocalEnvironment  # noqa: E402

from trace.events import Emitter  # noqa: E402
from trace.models_mock import build_mock_model  # noqa: E402
from trace.tracing_agent import TracingAgent  # noqa: E402

DEFAULT_TASK = "Fix the failing test in examples/sample-repo so that add(2, 3) == 5."


def _load_agent_config() -> dict:
    cfg = yaml.safe_load((REPO_ROOT / "upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _build_vibeproxy_model():
    from minisweagent.models.litellm_model import LitellmModel
    return LitellmModel(
        model_name="openai/" + os.getenv("VIBEPROXY_MODEL", "gpt-5.1-codex"),
        model_kwargs={
            "api_base": os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
            "api_key": os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
        },
        cost_tracking="ignore_errors",
    )


def _run_id() -> str:
    # No Date.now in scripts? This is a real process; time is fine here.
    return time.strftime("%Y%m%d-%H%M%S")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase-0 traced mini-swe-agent")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--cwd", default=str(REPO_ROOT / "examples" / "sample-repo"))
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")  # explicit: mini's own load targets the global dir

    run_dir = REPO_ROOT / "trace" / "runs" / _run_id()
    run_dir.mkdir(parents=True, exist_ok=True)

    emitter = Emitter(run_dir / "events.jsonl", clock=lambda: 0.0, console=True)

    if args.model == "mock":
        model = build_mock_model()
    else:
        model = _build_vibeproxy_model()

    env = LocalEnvironment(cwd=args.cwd)
    agent_cfg = _load_agent_config()
    agent_cfg["output_path"] = str(run_dir / "traj.json")
    agent = TracingAgent(model, env, emitter=emitter, **agent_cfg)

    try:
        agent.run(args.task)
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


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the mock end-to-end (the canonical deliverable)**

Run:
```bash
cd /Users/alberto/Work/Quiubo/harness && ./run.sh --model mock
```
Expected: a live stream printed to console ending in `action.done` for the
submit command and a final `run.finished ... ok=True ... exit_status=Submitted`;
paths to `events.jsonl` and `traj.json` printed.

- [ ] **Step 3: Verify the JSONL artifact parses and the bug got fixed**

Run:
```bash
cd /Users/alberto/Work/Quiubo/harness
python3 -c "import json,glob; f=sorted(glob.glob('trace/runs/*/events.jsonl'))[-1]; [json.loads(l) for l in open(f)]; print('jsonl OK:', f)"
grep -n "return a + b" examples/sample-repo/calculator.py && echo "bug fixed"
```
Expected: `jsonl OK: ...` and `bug fixed`.

- [ ] **Step 4: Reset the sample repo so the demo is repeatable**

Run:
```bash
cd /Users/alberto/Work/Quiubo/harness
git checkout examples/sample-repo/calculator.py
```
Expected: the file returns to `return a - b` (so re-running the demo starts from the failing state).

- [ ] **Step 5: Commit**

```bash
git add trace/run_traced.py
git commit -m "feat(trace): runner wiring mock + vibeproxy with explicit dotenv & config"
```

---

### Task 7: Learning log + README

**Files:**
- Create: `docs/learning-log.md`
- Create: `README.md` (repo root)

**Interfaces:**
- Consumes: nothing (documentation).
- Produces: the Phase-0 learning deliverable (spec §6) and a how-to-run README.

- [ ] **Step 1: Write the learning log, pre-seeded with seam prompts**

`docs/learning-log.md`:
```markdown
# Phase 0 — Learning Log

Fill these in by reading `upstream/src/minisweagent/` and by running
`./run.sh --model mock` and reading `trace/runs/<latest>/events.jsonl`.

## The loop (run / step)
- What makes `run()` stop?  (Answer: the last message's role is `"exit"` —
  default.py:118-120. `Submitted` is an `InterruptAgentFlow`, caught in `run()`,
  which appends an `exit` message and breaks.)
- What is one "step"?  (Answer: `step() = execute_actions(query())`.)

## The LLM seam (query / model.query)
- What does `model.query()` return, and how do actions attach to it?
  (Observed in `llm.return` events + `traj.json`: an assistant message whose
  `extra.actions` is the parsed tool calls.)
- When does a model call NOT happen even though the loop iterates?
  (Limit checks: `LimitsExceeded` / `TimeExceeded` before the call.)

## The shell seam (execute_actions / env.execute)
- How does an action dict become a real command + an observation?
  (`env.execute({"command": ...})` → `{output, returncode, exception_info}`;
  observation messages built by `model.format_observation_messages`.)
- Why did the final `action.done` need special handling?
  (The submit command makes `LocalEnvironment.execute` raise `Submitted`
  *before* returning — local.py `_check_finished`.)

## Interfaces I'd want to replace (feeds Phase 1 AgentRunner)
- (Notes on what the Model / Environment / Agent protocols would look like as a
  clean `AgentRunner` boundary.)

## VibeProxy run (bonus, manual)
- Did `--model vibeproxy` work? Did the endpoint accept `tools=[...]`
  (function-calling)? Record the outcome and any error verbatim.
```

- [ ] **Step 2: Write the repo README**

`README.md`:
```markdown
# harness — Phase 0: traced fork of mini-swe-agent

A learning-first agent harness. Phase 0 instruments a vendored, unmodified copy
of [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) (v2.4.2) with a
live event tracer, to understand the core agent loop's three seams.

## Run the mock demo (zero cost)

```bash
./run.sh --model mock
```

Streams events to the console and writes `trace/runs/<ts>/events.jsonl` and
`traj.json`. The mock model fixes the failing test in `examples/sample-repo`.
Reset between runs with `git checkout examples/sample-repo/calculator.py`.

## Run against VibeProxy (bonus)

Copy `.env.example` to `.env`, ensure VibeProxy is running on `:8317`, then:

```bash
./run.sh --model vibeproxy --task "Fix the failing test in examples/sample-repo."
```

## Layout
- `upstream/` — vendored mini-swe-agent, never edited.
- `trace/` — the tracer (events, agent overrides, mock model, runner).
- `examples/sample-repo/` — tiny repo with one failing test.
- `docs/` — spec, plan, and learning log.

## Tests
```bash
python3 -m pytest tests/ -v
```
```

- [ ] **Step 3: Run the test suite one final time**

Run: `cd /Users/alberto/Work/Quiubo/harness && python3 -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add docs/learning-log.md README.md
git commit -m "docs: phase-0 learning log and README"
```

---

## Self-Review

**1. Spec coverage:**

- §1 criterion 1 (vendored untouched, pinned, trace/ separate) → Task 1.
- §1 criterion 2 (mock prints stream + writes events.jsonl, zero cost, terminal seam) → Tasks 4, 5, 6.
- §1 criterion 3 (VibeProxy via flag, env only, bonus) → Task 6 (`_build_vibeproxy_model`).
- §1 criterion 4 (learning log) → Task 7.
- §2 three seams + reimplementation rationale → Task 5.
- §2 constructor `__init__(*, emitter, **kwargs)` + required templates from config → Tasks 5, 6.
- §3 event model + fields + `run.finished` in finally with ok/exception_* → Tasks 2, 5.
- §3 output_bytes derivation, output_path for traj.json → Tasks 5, 6.
- §4 mock cost_per_call=0.0, make_toolcall_output shape, submit sentinel → Task 4.
- §4 vibeproxy config (openai/ prefix, api_base, cost_tracking direct), explicit dotenv, no free-form model, no textbased fallback → Task 6.
- §5 JSONL loud startup failure, console never crashes → Task 2; Test A + Test B → Task 5.
- §6 learning log seam prompts → Task 7.

No gaps found.

**2. Placeholder scan:** No TBD/TODO/"add error handling" placeholders; every code step has complete code.

**3. Type consistency:**
- `Emitter.emit(type, **data)` — used identically in Tasks 2, 5, 6. ✓
- `TracingAgent(model, env, *, emitter, **kwargs)` — defined Task 5, called Tasks 5 (test), 6. ✓
- `build_mock_model() -> DeterministicToolcallModel` — defined Task 4, used Task 6. ✓
- `Event(seq, t, type, data)` + `.to_dict()` — defined Task 2, consumed in Emitter. ✓
- Event types (`run.started`, `llm.call`, `llm.return`, `action`, `action.done`, `run.finished`) — emitted in Task 5, asserted in Task 5 tests, matching spec §3. ✓

Clock ownership, noted for the implementer: the `Emitter` is constructed with a
placeholder `clock=lambda: 0.0`, and `TracingAgent.run()` installs the real
run-relative clock at run start via `emitter.set_clock(self._t)` (public method,
added in Task 2). This is intentional — the agent owns the run clock so `t` is
elapsed-since-run-start. Tests assert on `seq` ordering and event `type`, not on
`t` values, so they are independent of the clock source. Implementers should not
"fix" the placeholder lambda.
