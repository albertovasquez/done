# Observe-only Off-ramp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the agent from forcing a fix-workflow on read-only "check X" requests (issue #177).

**Architecture:** Defense-in-depth across three layers. L1: `ops_task` turns get an observe-first instance template instead of the SWE-bench "Please solve this issue" work-order — extracted into a shared leaf module so all routed run paths (ACP, dev CLI, opt-in cron) use it. L2: the router learns observe-vs-fix intent. L3: `systematic-debugging` gets an observe-only precondition off-ramp + a tightened description. L1+L3 are the real safety net; L2 is advisory.

**Tech Stack:** Python 3.11, pytest, mini-SWE-agent engine, Jinja2 templates (`{{task}}`).

## Global Constraints

- Work in the worktree `/Users/alberto/Work/Quiubo/harness/.worktrees/observe-offramp` (branch `fix/observe-offramp`). NEVER edit the primary checkout.
- Test command from worktree root: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (the worktree has no `.venv`; the primary venv's conftest resolves worktree modules by absolute path).
- New module `harness/instance_templates.py` must stay a LEAF: it may import only stdlib. It must NOT import `acp_agent`, `router`, `run_traced`, or `jobs.*` (cycle guard — same discipline as `textgate.py`/`permcheck.py`).
- Every instance template MUST preserve the `{{task}}` Jinja placeholder and end with the literal sentinel `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.
- The cron `mode` read is ADDITIVE: a job without `agent_options["mode"]` keeps the work-order default unchanged. No `jobs/model.py` change.
- No new task type, no keyword-heuristic code gate (rejected in spec).
- Baseline before starting: `tests/test_router.py tests/test_run_traced.py tests/test_system_skills.py tests/test_flows.py` = 39 passed.

---

### Task 1: Extract the instance-template leaf module (L1 scaffold, no behavior change)

Move `ANSWER_ONLY_INSTANCE` and `_instance_template_for` out of `acp_agent.py` into a new leaf module, and add the new `OBSERVE_FIRST_INSTANCE` constant + `ops_task` branch. `acp_agent.py` re-imports them, so its behavior is identical. This is the seam that lets dev-CLI and cron share the selection in later tasks.

**Files:**
- Create: `harness/instance_templates.py`
- Modify: `harness/acp_agent.py:46-69` (remove the two defs), `harness/acp_agent.py:22-34` (add import)
- Test: `tests/test_instance_templates.py` (new)

**Interfaces:**
- Produces: `harness.instance_templates.ANSWER_ONLY_INSTANCE: str`, `harness.instance_templates.OBSERVE_FIRST_INSTANCE: str`, `harness.instance_templates._instance_template_for(task_type: str, default: str) -> str`. Mapping: `code_explain` → `ANSWER_ONLY_INSTANCE`; `ops_task` → `OBSERVE_FIRST_INSTANCE`; anything else → `default`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_instance_templates.py`:

```python
import pytest
from harness.instance_templates import (
    ANSWER_ONLY_INSTANCE, OBSERVE_FIRST_INSTANCE, _instance_template_for,
)

DEFAULT = "Please solve this issue: {{task}}\nEdit the source code to resolve it."


@pytest.mark.parametrize(("task_type", "expected"), [
    ("code_explain", ANSWER_ONLY_INSTANCE),
    ("ops_task", OBSERVE_FIRST_INSTANCE),
    ("code_fix", DEFAULT),
    ("code_feature", DEFAULT),
    ("code_refactor", DEFAULT),
    ("chat_question", DEFAULT),
    ("ambiguous", DEFAULT),
])
def test_template_selection(task_type, expected):
    assert _instance_template_for(task_type, DEFAULT) == expected


def test_observe_first_keeps_task_placeholder_and_sentinel():
    assert "{{task}}" in OBSERVE_FIRST_INSTANCE
    assert "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in OBSERVE_FIRST_INSTANCE


def test_observe_first_is_read_only_imperative_not_work_order():
    low = OBSERVE_FIRST_INSTANCE.lower()
    # imperative read-only floor (ANSWER_ONLY strength), not a soft "ask"
    assert "do not" in low or "don't" in low
    assert "edit" in low and "create" in low and "delete" in low
    # the exact #177 anti-pattern must be forbidden in words
    assert "test suite" in low
    # must NOT carry the work-order framing
    assert "solve this issue" not in low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_instance_templates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.instance_templates'`.

- [ ] **Step 3: Create the leaf module**

Create `harness/instance_templates.py`:

```python
"""Per-task-type instance templates — the USER-turn framing injected every step.

LEAF module: stdlib-only imports. Do NOT import acp_agent / router / run_traced /
jobs.* (cycle guard, same as textgate.py / permcheck.py). The engine's default
instance_template (mini.yaml) reads "Please solve this issue: {{task}} … Edit the
source code to resolve it" — an every-turn work-order. We swap that framing per
task type so a read-only request is not treated as a fix job (issue #177).
"""
from __future__ import annotations

# code_explain: answer, don't act.
ANSWER_ONLY_INSTANCE = (
    "The user asked: {{task}}\n\n"
    "This is a QUESTION, not a work order. Investigate as needed — read files, "
    "run read-only commands — then ANSWER in words. Do NOT edit, create, or "
    "delete files to answer it. If a good answer would require changing code, "
    "say so and ask whether to proceed; do not start the change yourself. "
    "When you have answered, finish by issuing exactly: "
    "`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`."
)

# ops_task: observe and report; acting is the explicit, consent-gated exception.
# Read-only is an IMPERATIVE floor (ANSWER_ONLY strength) so work-order momentum
# can't read "ask first" as optional.
OBSERVE_FIRST_INSTANCE = (
    "The user asked: {{task}}\n\n"
    "Treat this as an OBSERVE request: inspect the relevant state and report what "
    "you find. Read files and run read-only commands (status, logs, heartbeat, "
    "PID, job state). Do NOT edit, create, or delete anything to investigate. "
    "Do not assume something is broken — if everything is healthy, say so and stop. "
    "Do NOT manufacture a reproduction: do not run the test suite to find a failing "
    "test that wasn't reported. If a fix turns out to be needed, STOP and ask first "
    "— describe the failure and ask whether to proceed; do not start the change "
    "yourself. When you have answered, finish by issuing exactly: "
    "`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`."
)


def _instance_template_for(task_type: str, default: str) -> str:
    """Pick the engine instance_template for this turn. code_explain → answer-only;
    ops_task → observe-first; every other task_type keeps the engine default."""
    if task_type == "code_explain":
        return ANSWER_ONLY_INSTANCE
    if task_type == "ops_task":
        return OBSERVE_FIRST_INSTANCE
    return default
```

- [ ] **Step 4: Point `acp_agent.py` at the leaf**

In `harness/acp_agent.py`, DELETE the block at lines 46-69 (the `# Answer-only instance template…` comment, the `ANSWER_ONLY_INSTANCE = (...)` constant, and the `_instance_template_for` function). Then add this import alongside the other `from harness…` imports (after line 34, `from harness.transcript import flatten_agent_messages`):

```python
from harness.instance_templates import (
    ANSWER_ONLY_INSTANCE, OBSERVE_FIRST_INSTANCE, _instance_template_for,
)
```

(`ANSWER_ONLY_INSTANCE` and `OBSERVE_FIRST_INSTANCE` are imported even though `acp_agent` only calls `_instance_template_for`, so any other module reading them via `acp_agent` keeps working and the names stay discoverable.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_instance_templates.py -q`
Expected: PASS (9 cases).

- [ ] **Step 6: Run the existing acp_agent tests to confirm no behavior change**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_agent.py -q`
Expected: `test_explain_turn_swaps_to_answer_only_template` PASS; `test_work_order_turn_keeps_engine_instance_template` will FAIL on `ops_task` (it asserts `ops_task == default`). That failure is EXPECTED and fixed in Task 2 — do not fix it here. All other acp_agent tests PASS. (If the import at Step 4 was wrong, you'll instead see an ImportError — fix that, it is not the expected failure.)

- [ ] **Step 7: Commit**

```bash
git add harness/instance_templates.py harness/acp_agent.py tests/test_instance_templates.py
git commit -m "feat(instance-templates): extract leaf + add OBSERVE_FIRST_INSTANCE (#177)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Update the existing acp_agent template test for the ops_task change

Task 1 intentionally broke `test_work_order_turn_keeps_engine_instance_template` (it asserts `ops_task` keeps the default). Fix the test to reflect the new contract and add an explicit `ops_task → OBSERVE_FIRST_INSTANCE` assertion for the ACP path. Also update the import lines that moved to the leaf.

**Files:**
- Modify: `tests/test_acp_agent.py:52,58-66,72` (imports + the work-order test)

**Interfaces:**
- Consumes: `harness.instance_templates._instance_template_for`, `ANSWER_ONLY_INSTANCE`, `OBSERVE_FIRST_INSTANCE` (Task 1).

- [ ] **Step 1: Update the failing test + imports**

In `tests/test_acp_agent.py`:

(a) At line 52, change the import source from `acp_agent` to the leaf:
```python
    from harness.instance_templates import _instance_template_for, ANSWER_ONLY_INSTANCE
```

(b) Replace the body of `test_work_order_turn_keeps_engine_instance_template` (lines 58-66) with — drop `"ops_task"` from the default-keeping set and import from the leaf:
```python
def test_work_order_turn_keeps_engine_instance_template():
    """A real work order (code_fix/feature/refactor) keeps the engine default
    template unchanged — the gate must not handicap turns the user DID ask to act
    on. ops_task is no longer here: it gets the observe-first template (Task 1)."""
    from harness.instance_templates import _instance_template_for

    default = "Please solve this issue: {{task}}"
    for tt in ("code_fix", "code_feature", "code_refactor"):
        assert _instance_template_for(tt, default) == default


def test_ops_task_turn_gets_observe_first_template():
    """A read-only ops_task (e.g. 'check if the cron is firing') must NOT get the
    'Please solve this issue' work-order — it gets the observe-first template (#177)."""
    from harness.instance_templates import _instance_template_for, OBSERVE_FIRST_INSTANCE

    assert _instance_template_for("ops_task", "Please solve this issue: {{task}}") is OBSERVE_FIRST_INSTANCE
```

(c) At line 72 (inside `test_answer_only_template_keeps_task_placeholder_and_forbids_edits`), change the import source:
```python
    from harness.instance_templates import ANSWER_ONLY_INSTANCE
```

- [ ] **Step 2: Run the acp_agent tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_agent.py -q`
Expected: PASS (all, including the new `test_ops_task_turn_gets_observe_first_template`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_acp_agent.py
git commit -m "test(acp): ops_task gets observe-first template, not work-order (#177)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Thread task_type through the dev CLI (run_traced.py)

`run_traced.py` classifies (`route_and_dispatch` has `cls.task_type`) but discards it — its `run_agent` builds the runner with the raw `mini.yaml` template. Thread `task_type` into `run_agent` and apply `_instance_template_for`, so the dev CLI matches the ACP path.

**Files:**
- Modify: `harness/run_traced.py:111-116` (the dispatch call site), `harness/run_traced.py:197-203` (the `run_agent` closure)
- Test: `tests/test_run_traced.py` (add two cases + update the `run_agent` spy at `:26`)

**Interfaces:**
- Consumes: `harness.instance_templates._instance_template_for` (Task 1); `route_and_dispatch(..., run_agent=...)` already calls `run_agent(prompt, skill_block=load.block)` at line 116.
- Produces: `run_traced._instance_template_cfg(agent_cfg, task_type) -> dict` (a COPY with `instance_template` set — never mutates the caller's dict, because `agent_cfg` is shared module/main-scope state built once at `run_traced.py:164`). `run_agent` accepts `task_type: str` and builds the runner from that copy. The `route_and_dispatch` `run_agent` test double must also accept `task_type`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_run_traced.py`:

```python
def test_run_traced_instance_template_cfg_observe_first_for_ops_task():
    """The dev CLI path must apply the observe-first template for ops_task, not the
    raw mini.yaml work-order (parity with the ACP path; #177). Returns a COPY — the
    shared agent_cfg (run_traced.py:164) must not be mutated."""
    import harness.run_traced as rt
    from harness.instance_templates import OBSERVE_FIRST_INSTANCE

    agent_cfg = {"instance_template": "Please solve this issue: {{task}}", "step_limit": 7}
    out = rt._instance_template_cfg(agent_cfg, "ops_task")
    assert out["instance_template"] == OBSERVE_FIRST_INSTANCE
    assert out["step_limit"] == 7                                  # other keys preserved
    assert agent_cfg["instance_template"] == "Please solve this issue: {{task}}"  # NOT mutated


def test_run_traced_instance_template_cfg_leaves_work_order_for_code_fix():
    import harness.run_traced as rt

    default = "Please solve this issue: {{task}}"
    out = rt._instance_template_cfg({"instance_template": default}, "code_fix")
    assert out["instance_template"] == default
```

> Implementation note: rather than reconstruct the whole `main()` wiring in a test, Task 3 introduces a tiny pure helper `_apply_instance_template(agent_cfg, task_type)` in `run_traced.py` that the `run_agent` closure calls. The test targets that helper (pure, no engine), and the closure/threading is covered by the existing `route_and_dispatch` tests staying green.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_run_traced.py::test_run_traced_instance_template_cfg_observe_first_for_ops_task -q`
Expected: FAIL — `AttributeError: module 'harness.run_traced' has no attribute '_instance_template_cfg'`.

- [ ] **Step 3: Add the helper and thread task_type**

In `harness/run_traced.py`, add the import near the top (with the other `from harness…` imports):
```python
from harness.instance_templates import _instance_template_for
```

Add the pure helper (module level, e.g. just above `route_and_dispatch`). It returns a
COPY — `agent_cfg` (`run_traced.py:164`) is shared main-scope state, so mutating it in
place would persist across calls; never do that:
```python
def _instance_template_cfg(agent_cfg: dict, task_type: str) -> dict:
    """Return a COPY of agent_cfg with instance_template chosen for this task_type.
    Mirrors the ACP path (acp_agent.py:716) so the dev CLI doesn't fall through to the
    raw mini.yaml work-order on read-only ops_task requests (#177). Never mutates the
    caller's dict — agent_cfg is built once at module scope and reused."""
    return {**agent_cfg, "instance_template":
            _instance_template_for(task_type, agent_cfg.get("instance_template", ""))}
```

Change the `run_agent` closure (lines 197-203) to accept `task_type` and build from the copy:
```python
    def run_agent(prompt, skill_block="", task_type=""):
        runner = MiniSweAgentRunner(model, env,
                                    agent_cfg=_instance_template_cfg(agent_cfg, task_type))
        try:
            for event in runner.run(prompt, skill_block=skill_block,
                                    persona_block=persona_block,
                                    memory_block=memory_block,
                                    base_block=base_block):
                emitter.write_renumbered(event)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
```

In `route_and_dispatch`, change the dispatch call (line 116) to pass the classified task type:
```python
        run_agent(prompt, skill_block=load.block, task_type=cls.task_type)
```

Update the `run_agent` test double in `tests/test_run_traced.py:26` to accept the new
kwarg (otherwise every existing route test throws `TypeError: unexpected keyword
'task_type'`):
```python
    def run_agent(prompt, skill_block="", task_type=""):
        calls.append(prompt)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_run_traced.py -q`
Expected: PASS (the new test + all existing route_and_dispatch tests).

- [ ] **Step 5: Commit**

```bash
git add harness/run_traced.py tests/test_run_traced.py
git commit -m "feat(run): apply observe-first template on ops_task in dev CLI (#177)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Per-job observe mode in the cron executor

The cron path has no router, so it can't classify intent. Default stays work-order. Add an opt-in: when the job's `AgentTurn.agent_options["mode"] == "observe"`, `run_turn` builds the runner with `OBSERVE_FIRST_INSTANCE`. Additive — no `mode` key ⇒ unchanged. Mirrors the existing `wall_budget` threading (read at dispatch, guarded by `_accepts_kwarg`).

**Files:**
- Modify: `harness/jobs/executor.py:136-137` (`run_turn` signature), `harness/jobs/executor.py:169` (`agent_cfg`), `harness/jobs/executor.py:249-258` (dispatch site)
- Test: `tests/jobs/test_executor.py` (add a case)

**Interfaces:**
- Consumes: `harness.instance_templates.OBSERVE_FIRST_INSTANCE` (Task 1); `AgentTurn.agent_options: dict` (`jobs/model.py:22`); `_accepts_kwarg(fn, name)` (already in `executor.py`, used for `wall_budget`).
- Produces: `run_turn(..., mode: str | None = None)` — when `mode == "observe"`, the runner's `agent_cfg["instance_template"]` is `OBSERVE_FIRST_INSTANCE`.

- [ ] **Step 1: Write the failing test**

Add to `tests/jobs/test_executor.py`:

```python
def test_observe_mode_passed_from_agent_options():
    """A cron AgentTurn with agent_options={'mode':'observe'} must hand mode through
    to run_turn; absent mode must not (default work-order). (#177)"""
    seen = {}
    deps = ex.Deps(
        resolve_workspace=lambda pid: __import__("pathlib").Path("/ws/fred"),
        resolve_model=lambda *a, **k: "model-X",
        compose=lambda ws: ("PB", "MB", ws),
        run_turn=lambda *, model_id, workspace, persona_block, memory_block, message, mode=None: (
            seen.setdefault("mode", mode)
        ),
        notify=lambda **k: None,
    )
    job = _job(payload=m.AgentTurn(message="check cron", agent_options={"mode": "observe"}))
    ex.run_headless_turn(job, deps=deps)
    assert seen["mode"] == "observe"


def test_no_mode_defaults_to_none():
    seen = {}
    deps = ex.Deps(
        resolve_workspace=lambda pid: __import__("pathlib").Path("/ws/fred"),
        resolve_model=lambda *a, **k: "model-X",
        compose=lambda ws: ("PB", "MB", ws),
        run_turn=lambda *, model_id, workspace, persona_block, memory_block, message, mode=None: (
            seen.setdefault("mode", mode)
        ),
        notify=lambda **k: None,
    )
    ex.run_headless_turn(_job(), deps=deps)   # default payload: no agent_options
    assert seen["mode"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/jobs/test_executor.py::test_observe_mode_passed_from_agent_options -q`
Expected: FAIL — `run_turn` is called without `mode` (the dispatch site doesn't read `agent_options` yet), so `seen["mode"]` is never set → `KeyError`.

- [ ] **Step 3: Read mode at the dispatch site**

In `harness/jobs/executor.py`, in `run_headless_turn` after the `_wall_budget` block (after line 255, where `_turn_kwargs` is assembled), add the `mode` read mirroring the `wall_budget` guard:
```python
    _mode = job.payload.agent_options.get("mode")  # AgentTurn only; e.g. "observe"
    if _mode is not None and _accepts_kwarg(deps.run_turn, "mode"):
        _turn_kwargs["mode"] = _mode
```
(Place it immediately before the existing `deps.run_turn(**_turn_kwargs)` line.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/jobs/test_executor.py -q`
Expected: PASS (both new cases + all existing executor tests; the existing injected-`run_turn` doubles don't accept `mode`, so `_accepts_kwarg` correctly skips passing it to them).

- [ ] **Step 5: Add the template helper + unit test (module level)**

`run_turn` is nested inside `_default_deps` (`executor.py:95→136`). To avoid a
closure-scope trap and keep it directly testable, put the helper and its import at
**module level** (top of `harness/jobs/executor.py`, next to `_accepts_kwarg` at `:64`),
NOT inside `_default_deps`.

(a) Add the import at module top (with the other top-level imports, NOT inside a function):
```python
from harness.instance_templates import OBSERVE_FIRST_INSTANCE
```

(b) Add the helper at module level (e.g. just below `_accepts_kwarg`):
```python
def _observe_or_default_cfg(cfg: dict, mode: str | None) -> dict:
    """If the job opted into observe mode, return a COPY with the observe-first
    instance_template; otherwise return cfg untouched (default work-order). (#177)"""
    if mode == "observe":
        return {**cfg, "instance_template": OBSERVE_FIRST_INSTANCE}
    return cfg
```

(c) Add a direct unit test (the existing `test_default_deps_constructs` never runs a
turn, so without this a misplaced helper/import wouldn't fail until a real cron fires):
```python
def test_observe_or_default_cfg_swaps_only_for_observe():
    from harness.jobs.executor import _observe_or_default_cfg
    from harness.instance_templates import OBSERVE_FIRST_INSTANCE

    base = {"instance_template": "Please solve this issue: {{task}}", "step_limit": 9}
    assert _observe_or_default_cfg(base, "observe")["instance_template"] is OBSERVE_FIRST_INSTANCE
    assert _observe_or_default_cfg(base, "observe")["step_limit"] == 9       # other keys kept
    assert _observe_or_default_cfg(base, None) is base                       # default untouched
    assert base["instance_template"] == "Please solve this issue: {{task}}"  # not mutated
```

- [ ] **Step 6: Honor `mode` inside the real `run_turn`**

(a) Change the `run_turn` signature (line 136-137) to accept `mode`:
```python
    def run_turn(*, model_id: str | None, workspace: Path, persona_block: str,
                 memory_block: str, message: str, wall_budget: int | None = None,
                 mode: str | None = None) -> None:
```

(b) Change the `agent_cfg=_load_agent_cfg()` argument to `build_persona_agent` (line 169) to run through the helper. Replace:
```python
            agent_cfg=_load_agent_cfg(),
```
with:
```python
            agent_cfg=_observe_or_default_cfg(_load_agent_cfg(), mode),
```

- [ ] **Step 7: Run the full jobs suite to verify no regression**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/jobs/ -q`
Expected: PASS (all). `test_default_deps_constructs` still passes — the real `run_turn` now accepts `mode` but defaults it to `None`.

- [ ] **Step 8: Commit**

```bash
git add harness/jobs/executor.py tests/jobs/test_executor.py
git commit -m "feat(jobs): opt-in observe mode via agent_options['mode'] (#177)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: systematic-debugging off-ramp + tightened description (L3)

Add an observe-only precondition at the top of the skill body so the four-phase fix machine only engages when a failure is actually reported, and tighten the frontmatter `description` (the text the router classifies on) so it stops over-attaching to "check X". This is the cross-cutting backstop: it corrects behavior even when L2 mis-attaches.

**Files:**
- Modify: `harness/skills/systematic-debugging/SKILL.md:3` (description), `harness/skills/systematic-debugging/SKILL.md:16-22` (insert precondition before "The Iron Law")
- Test: `tests/test_system_skills.py` (add content assertions), `tests/test_router.py:6` (refresh stale description fixture)

**Interfaces:**
- Consumes: nothing (markdown). The router reads frontmatter `description` via `skills.py:51`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_system_skills.py`:

```python
def test_systematic_debugging_has_observe_only_offramp():
    """The skill must NOT force a fix-workflow on read-only checks: an explicit
    precondition gates it on a *reported* failure, and the description no longer
    invites attachment to any 'unexpected behavior' (#177)."""
    from pathlib import Path
    import harness  # to locate the package root

    root = Path(harness.__file__).resolve().parent
    text = (root / "skills" / "systematic-debugging" / "SKILL.md").read_text()
    low = text.lower()

    # off-ramp precondition present in the body
    assert "reported" in low and "observe" in low
    assert "do not run the test suite" in low
    # description tightened: no longer the broad "any ... unexpected behavior" hook
    assert "unexpected behavior" not in low.split("# systematic debugging")[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_system_skills.py::test_systematic_debugging_has_observe_only_offramp -q`
Expected: FAIL — current description contains "unexpected behavior" and the body has no observe precondition.

- [ ] **Step 3: Tighten the frontmatter description**

In `harness/skills/systematic-debugging/SKILL.md`, replace line 3:
```
description: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes
```
with:
```
description: Use when there is a REPORTED failing behavior to fix — a bug, failing test, or error. NOT for read-only status checks ("is X working", "did Y fire") — those are observe requests, inspect and answer directly.
```

- [ ] **Step 4: Insert the observe-only precondition before "The Iron Law"**

In the same file, between the `## Overview` block and `## The Iron Law` (i.e. after the line `**Violating the letter of this process is violating the spirit of debugging.**` and before `## The Iron Law`), insert:
```markdown
## Precondition: only for a reported failure

This workflow applies ONLY when there is a **reported failing behavior** — an error,
a failing test, or broken output the user pointed at. The four phases below assume a
confirmed failure exists.

**If the request is to observe / check / report status with no reported failure**
(e.g. "check if the cron is firing", "is X working", "show me the status of Y"):
do NOT enter this workflow. Inspect the relevant state and answer directly. Do NOT
manufacture a reproduction — in particular, **do not run the test suite to find a
failing test that wasn't reported.** There is nothing to fix until a failure is
reported.
```

- [ ] **Step 5: Refresh the stale router-test fixture**

`tests/test_router.py:6` hand-builds a `SkillMeta("systematic-debugging", "Use when
encountering any bug, test failure, or unexpected behavior")` — the OLD description.
It's a fixture (not an assertion on the real file), so Task 5 doesn't break it, but it
now misrepresents the skill. Update the string to the new tightened description so the
fixture matches reality:
```python
    SkillMeta("systematic-debugging", "Use when there is a REPORTED failing behavior to fix — a bug, failing test, or error. NOT for read-only status checks."),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_system_skills.py tests/test_router.py -q`
Expected: PASS (new test + existing). If any other existing test asserts the old description string verbatim, update it to the new text.

- [ ] **Step 7: Commit**

```bash
git add harness/skills/systematic-debugging/SKILL.md tests/test_system_skills.py tests/test_router.py
git commit -m "fix(skill): systematic-debugging observe-only off-ramp + tighter desc (#177)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Router observe-vs-fix guidance (L2)

Teach the router's `_system_prompt` to distinguish observe-intent from fix-intent within `ops_task` and not to auto-attach debugging skills without a reported failure. Advisory (the cheap model isn't deterministic), so the test asserts the GUIDANCE TEXT is present rather than a model decision.

**Files:**
- Modify: `harness/router.py:59-82` (`_system_prompt`)
- Test: `tests/test_router.py` (add a case)

**Interfaces:**
- Consumes: `harness.router._system_prompt(catalog) -> str`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_router.py`:

```python
def test_system_prompt_teaches_observe_vs_fix():
    """The router prompt must steer observe-intent ('check', 'is X working') away
    from debugging skills, so read-only requests don't pull in systematic-debugging
    (#177). Advisory text check, not a model-output check."""
    from harness.router import _system_prompt
    prompt = _system_prompt([]).lower()
    assert "observe" in prompt
    assert "check" in prompt
    assert "do not" in prompt or "don't" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_router.py::test_system_prompt_teaches_observe_vs_fix -q`
Expected: FAIL — the current `_system_prompt` has no observe-vs-fix guidance.

- [ ] **Step 3: Add the guidance to `_system_prompt`**

In `harness/router.py`, inside `_system_prompt` (the returned string), add this sentence to the `ops_task` guidance — insert it right after the existing sentence that ends `"...let the agent look."` (around line 68), before the `"Reserve 'ambiguous'..."` sentence:
```python
        "Within ops_task, distinguish OBSERVE-intent (\"check\", \"is X working\", "
        "\"show status\", \"did Y fire\", \"is the cron firing\") from FIX-intent (a "
        "reported failure, error, or \"X is broken\"). For an observe-only request, "
        "do NOT attach debugging skills (e.g. systematic-debugging) — only attach "
        "them when the user reports a failing behavior. "
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_router.py -q`
Expected: PASS (new test + all existing router tests).

- [ ] **Step 5: Commit**

```bash
git add harness/router.py tests/test_router.py
git commit -m "feat(router): observe-vs-fix intent guidance for ops_task (#177)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Full-suite regression + final verification

Confirm the whole change is green and nothing outside the touched modules regressed.

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all). If anything fails, it is in scope — fix it before proceeding (most likely an existing test that asserted the old skill description or the old `ops_task == default` mapping; update it to the new contract).

- [ ] **Step 2: Confirm the leaf module has no import cycle**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -c "import harness.instance_templates; print('leaf ok')"`
Expected: prints `leaf ok` with no ImportError.

- [ ] **Step 3: Confirm the primary checkout is untouched**

Run: `cd /Users/alberto/Work/Quiubo/harness && git status --porcelain`
Expected: empty output (all work is in the worktree, primary is clean).

- [ ] **Step 4: Manual behavioral acceptance (optional, documents the #177 repro)**

In a live `dn` session: ask "check if the cron was firing". Expected: it inspects daemon/heartbeat/job state and answers, WITHOUT asking for a bug report or running pytest. (Record the result in the PR description; this is the real-world proof the fix lands.)

---

## Self-Review

**Spec coverage:**
- L1 ops_task observe-first template → Task 1 (leaf + constant) + Task 2 (ACP test).
- L1 dev-CLI path → Task 3. L1 cron opt-in `mode` → Task 4.
- L2 router guidance → Task 6.
- L3 off-ramp + tightened description → Task 5.
- Sentinel/`{{task}}` preservation → Task 1 Step 1 content test. No-manufactured-repro pinned → Task 1 + Task 5 content tests.
- Multi-path coverage (ACP/dev-CLI/cron, worker out-of-scope) → Tasks 1–4. Worker correctly untouched.
- Regression baseline → Task 7.

**Placeholder scan:** No TBD/TODO; every code step shows the full code; every test step shows the assertion and the exact command + expected result.

**Type consistency:** `_instance_template_for(task_type, default)`, `ANSWER_ONLY_INSTANCE`, `OBSERVE_FIRST_INSTANCE` are named identically across Tasks 1–4. `run_turn(..., mode: str | None = None)` matches the dispatch read `job.payload.agent_options.get("mode")` (Task 4). `_instance_template_cfg(agent_cfg, task_type)` (Task 3) and `_observe_or_default_cfg(cfg, mode)` (Task 4) both RETURN copies — neither mutates the caller's dict (both shared/reused). Each is defined and consumed only within its own task.
