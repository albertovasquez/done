# TUI Plan Checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface a live, model-authored plan checklist in the TUI's pinned ActivityRegion — steps tick off in place and the checklist disappears when the agent goes idle.

**Architecture:** The agent emits ACP's native `update_plan(...)` (full-snapshot, replace-semantics). `render.py` turns the `AgentPlanUpdate` into a `RenderedItem(kind="plan")`; the `state.py` reducer folds it (via the existing `ItemReceived` path) into a NEW `AgentSnapshot.plan` field, replacing it wholesale; `activity_region.py` shows the existing `TaskTree` widget when `snap.plan` is non-empty. The tool-call reducer (which appends to `snapshot.tasks`) is untouched. `app.py` is not modified.

**Tech Stack:** Python 3.11, Textual, the `acp` package (`update_plan`/`plan_entry`/`AgentPlanUpdate`/`PlanEntry`), pytest.

## Global Constraints

- Pure core (`render.py`, `state.py`) stays Textual-free, async-free, dataclass-only — exhaustively unit-testable with `SimpleNamespace`/`_named(...)` stubs.
- `render_update` dispatches by `type(update).__name__` — NOT by a `session_update` attribute. New code follows type-name dispatch.
- ACP `plan` update is a full snapshot: each `update_plan(...)` replaces the entire plan. `AgentSnapshot.plan` uses replace-semantics; `tasks`/`tools` keep append behavior.
- ACP plan status values: `pending` / `in_progress` / `completed`. Map to TaskItem status: `pending`→`pending`, `in_progress`→`in_progress`, `completed`→`done`. No `failed` state in plans.
- Clearing is turn-START-driven: `TurnStarted` resets `plan=()`. `TurnEnded` does NOT clear it; the region hides on idle.
- No file persistence. No bespoke plan tool. No priority rendering. `app.py` untouched.
- Test command (run with the worktree as cwd; the venv lives in the primary checkout): `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: Render the `plan` update into a `RenderedItem`

**Files:**
- Modify: `harness/tui/render.py` (dataclass at lines 13-21; dispatch in `render_update` ending at line 61)
- Test: `tests/test_tui_render.py` (replace the test at line 56-57)

**Interfaces:**
- Consumes: an ACP `AgentPlanUpdate` (duck-typed): `.entries` is an iterable of objects each with `.content: str` and `.status: str`.
- Produces: `RenderedItem(kind="plan", entries=tuple[tuple[str, str], ...])` where each inner tuple is `(content, status)`. Adds a new field `entries: tuple[tuple[str, str], ...] = ()` to `RenderedItem`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_tui_render.py`, REPLACE the existing `test_render_unknown_returns_none` (lines 56-57) with these three tests:

```python
def test_render_plan_update():
    entry_a = NS(content="Push + PR", status="in_progress")
    entry_b = NS(content="CI + merge", status="pending")
    u = _named("AgentPlanUpdate", entries=[entry_a, entry_b])
    assert render_update(u) == RenderedItem(
        kind="plan",
        entries=(("Push + PR", "in_progress"), ("CI + merge", "pending")),
    )


def test_render_plan_update_empty():
    u = _named("AgentPlanUpdate", entries=[])
    assert render_update(u) == RenderedItem(kind="plan", entries=())


def test_render_unknown_returns_none():
    assert render_update(_named("SomeFutureUpdate", foo=1)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_render.py -q`
Expected: FAIL — `test_render_plan_update` fails because `RenderedItem` has no `entries` field / `render_update` returns `None` for `AgentPlanUpdate`.

- [ ] **Step 3: Add the `entries` field to `RenderedItem`**

In `harness/tui/render.py`, the `RenderedItem` dataclass (lines 13-21). Update the docstring kind list and add the field:

```python
@dataclass(frozen=True)
class RenderedItem:
    kind: str                 # "message" | "thought" | "user" | "tool" | "tool_update" | "plan"
    text: str = ""            # message/thought/user body
    id: str = ""              # tool_call_id (tool / tool_update correlation)
    title: str = ""           # "$ <command>" (tool)
    status: str = ""          # pending|in_progress|completed|failed
    body: str = ""            # tool output (tool_update)
    entries: tuple[tuple[str, str], ...] = ()   # plan: ((content, status), …)
```

- [ ] **Step 4: Add the plan branch to `render_update`**

In `harness/tui/render.py`, inside `render_update`, immediately BEFORE the final `return None` (line 61), add:

```python
    if name == "AgentPlanUpdate":
        entries = tuple(
            (getattr(e, "content", "") or "", str(getattr(e, "status", "")))
            for e in (getattr(update, "entries", None) or [])
        )
        return RenderedItem(kind="plan", entries=entries)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_render.py -q`
Expected: PASS (all render tests green).

- [ ] **Step 6: Commit**

```bash
git add harness/tui/render.py tests/test_tui_render.py
git commit -m "feat(tui): render ACP plan update into RenderedItem(kind=plan)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Reduce the plan item into a new `AgentSnapshot.plan` field

**Files:**
- Modify: `harness/tui/state.py` (`AgentSnapshot` lines 59-71; `TurnStarted` reset at line 161-162; `ItemReceived` handling lines 175-210)
- Test: `tests/test_tui_state.py` (add tests near the existing reducer tests)

**Interfaces:**
- Consumes: `RenderedItem(kind="plan", entries=(("Push + PR","in_progress"), …))` from Task 1, via `ItemReceived`.
- Produces: `AgentSnapshot.plan: tuple[TaskItem, ...]` (replace-semantics). Each entry → `TaskItem(label=content, status=<mapped>, tool_id="")`. Status map: `pending`→`pending`, `in_progress`→`in_progress`, `completed`→`done`, unknown→`pending`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_tui_state.py`, add (the imports `reduce, TurnStarted, TurnEnded, ItemReceived` and `RenderedItem` already exist at the top of the reducer test block):

```python
def test_plan_item_sets_plan_field():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan", entries=(
        ("Push + PR", "in_progress"),
        ("CI + merge", "pending"),
        ("Sync + prune", "completed"),
    ))))
    a = _active(fs)
    assert [(t.label, t.status) for t in a.plan] == [
        ("Push + PR", "in_progress"),
        ("CI + merge", "pending"),
        ("Sync + prune", "done"),
    ]
    assert all(t.tool_id == "" for t in a.plan)


def test_plan_update_replaces_not_appends():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan",
        entries=(("A", "in_progress"), ("B", "pending")))))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan",
        entries=(("A", "completed"), ("B", "in_progress")))))
    a = _active(fs)
    assert [(t.label, t.status) for t in a.plan] == [("A", "done"), ("B", "in_progress")]


def test_plan_does_not_touch_tasks():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ echo hi", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan",
        entries=(("Step one", "in_progress"),))))
    a = _active(fs)
    assert len(a.tasks) == 1 and a.tasks[0].label == "$ echo hi"   # tool task untouched
    assert len(a.plan) == 1 and a.plan[0].label == "Step one"


def test_turn_started_clears_plan():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan",
        entries=(("Step one", "in_progress"),))))
    assert len(_active(fs).plan) == 1
    fs = reduce(fs, TurnStarted())
    assert _active(fs).plan == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_state.py -q`
Expected: FAIL — `AgentSnapshot` has no `plan` attribute.

- [ ] **Step 3: Add the `plan` field to `AgentSnapshot`**

In `harness/tui/state.py`, `AgentSnapshot` (lines 59-71). Add the field after `tasks` (line 68):

```python
    tasks: tuple[TaskItem, ...] = ()
    plan: tuple[TaskItem, ...] = ()
```

- [ ] **Step 4: Add a plan-status mapper and the reducer branch**

In `harness/tui/state.py`, add a small pure helper near `_task_status_from_tool` (line 155):

```python
def _plan_task_status(raw: str) -> str:
    return {"pending": "pending", "in_progress": "in_progress",
            "completed": "done"}.get(str(raw), "pending")
```

Then in `_reduce_agent`, inside the `ItemReceived` block, add a `kind == "plan"` branch alongside the existing `kind == "tool"` / `kind == "tool_update"` branches (after the `message` branch, before `tool`):

```python
        if kind == "plan":
            entries = getattr(item, "entries", ()) or ()
            plan = tuple(
                TaskItem(label=content, status=_plan_task_status(status), tool_id="")
                for content, status in entries
            )
            return replace(a, plan=plan)
```

- [ ] **Step 5: Clear `plan` on `TurnStarted`**

In `harness/tui/state.py`, the `TurnStarted` branch (lines 160-162). Add `plan=()` to the reset:

```python
    if isinstance(event, TurnStarted):
        return replace(a, state=AgentState.THINKING, activity_label="Thinking",
                       tool=None, decision=None, tasks=(), tools=(), plan=(), elapsed=0.0)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_state.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): fold plan item into AgentSnapshot.plan (replace-semantics)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Show the TaskTree when a plan exists

**Files:**
- Modify: `harness/tui/widgets/activity_region.py` (`update_from`, lines 50-77)
- Test: `tests/test_tui_activity_region.py` (create — pure snapshot-driven assertion of the display decision)

**Interfaces:**
- Consumes: `AgentSnapshot.plan` (from Task 2).
- Produces: extracts the display decision into a pure static method `ActivityRegion.show_plan(snap) -> bool` so it is unit-testable without mounting Textual; `update_from` calls it and renders `snap.plan` into the `TaskTree`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui_activity_region.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.state import AgentSnapshot, AgentState, TaskItem
from harness.tui.widgets.activity_region import ActivityRegion


def _snap(state, plan=()):
    return AgentSnapshot(id="default", name="agent", state=state, plan=plan)


def test_show_plan_true_when_working_and_plan_present():
    snap = _snap(AgentState.RUNNING_TOOL, plan=(TaskItem(label="A", status="in_progress"),))
    assert ActivityRegion.show_plan(snap) is True


def test_show_plan_false_when_no_plan():
    snap = _snap(AgentState.RUNNING_TOOL, plan=())
    assert ActivityRegion.show_plan(snap) is False


def test_show_plan_false_when_idle_even_with_plan():
    snap = _snap(AgentState.DONE, plan=(TaskItem(label="A", status="done"),))
    assert ActivityRegion.show_plan(snap) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_activity_region.py -q`
Expected: FAIL — `ActivityRegion` has no attribute `show_plan`.

- [ ] **Step 3: Add the `show_plan` static method**

In `harness/tui/widgets/activity_region.py`, add a static method to `ActivityRegion` (next to `is_idle`, after line 43):

```python
    @staticmethod
    def show_plan(snap: AgentSnapshot) -> bool:
        return snap is not None and snap.state in _WORKING and bool(snap.plan)
```

- [ ] **Step 4: Wire it into `update_from`**

In `harness/tui/widgets/activity_region.py`, `update_from`. REPLACE the default-view block (lines 67-71, the part that currently hard-codes `task_tree.display = False`) with:

```python
        show_tools = self._details and bool(snap.tools)
        show_plan = self.show_plan(snap)
        # Default view = status line + plan checklist (when the agent emitted one).
        # The status line carries '· N done'; ctrl+o reveals the per-tool list.
        task_tree.display = show_plan
        tools_container.display = show_tools

        if show_plan:
            task_tree.update_tasks(snap.plan)
```

(Leave the `if show_tools:` block that follows it unchanged.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_activity_region.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/tui/widgets/activity_region.py tests/test_tui_activity_region.py
git commit -m "feat(tui): show TaskTree checklist when snapshot has a plan

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Teach the agent to emit a plan (system prompt)

**Files:**
- Modify: `harness/base_prompt.py` (`BASE_POLICY`, lines 10-34 — append a bullet under "# Working principles")
- Test: `tests/test_base_prompt.py` (add an assertion that the instruction is present)

**Interfaces:**
- Consumes: nothing (static prompt text).
- Produces: a "# Working principles" bullet instructing the agent to use the plan update for multi-step work.

- [ ] **Step 1: Write the failing test**

In `tests/test_base_prompt.py`, add:

```python
def test_base_prompt_instructs_plan_for_multistep():
    out = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="OS")
    low = out.lower()
    assert "multi-step" in low and "plan" in low
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: FAIL — `"multi-step"` not in the prompt.

- [ ] **Step 3: Add the bullet to `BASE_POLICY`**

In `harness/base_prompt.py`, inside `BASE_POLICY`, add this as the last bullet under "# Working principles" (immediately before the closing `"""` at line 34, after the line ending `…every changed line should trace to the task.`):

```python
- For multi-step work, publish a short plan up front (one entry per step) and \
keep it current: mark the active step in progress and finished steps complete as \
you go. Skip the plan for single-step or trivial work.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/base_prompt.py tests/test_base_prompt.py
git commit -m "feat(prompt): instruct agent to publish a plan for multi-step work

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Emit the plan from the agent loop (wire it to ACP)

**Files:**
- Modify: `harness/acp_agent.py` (the turn loop that calls `self._conn.session_update(session_id, …)`)
- Test: covered by full-suite green + manual run (the emit site is async ACP plumbing; the pure decision logic is already tested in Tasks 1-2).

**Interfaces:**
- Consumes: `acp.update_plan`, `acp.plan_entry` (verified present in the installed package).
- Produces: nothing new for the TUI — it sends `AgentPlanUpdate` over the same channel as message/tool updates, which Task 1 already renders.

> **NOTE — scope guard:** This task wires the *transport*. The agent decides *when/what* to emit via the system-prompt instruction (Task 4). If the agent backend already forwards a native plan/todo tool call as an ACP plan update (some ACP agents do), this task may reduce to verifying that pass-through and NOT adding a manual emit. The implementer MUST first check how the loop currently turns the model's output into `session_update` calls before adding code — do not duplicate an existing path.

- [ ] **Step 1: Inspect the emit path**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -c "import acp; print(acp.update_plan, acp.plan_entry)"`
Read `harness/acp_agent.py` around the `session_update` call sites (lines ~134, 191-233, 280, 320-349). Determine whether plan updates already pass through (model → ACP) or need an explicit emit.

- [ ] **Step 2: If an explicit emit is needed, add the helper call**

Only if no pass-through exists: at the point where the loop has a structured plan from the model, send it with:

```python
await self._conn.session_update(
    session_id,
    acp.update_plan([
        acp.plan_entry(step["content"], status=step.get("status", "pending"))
        for step in plan_steps
    ]),
)
```

Match the exact `session_update` invocation style already in the file (sync-vs-async wrapper, `run_coroutine_threadsafe` if that's the local idiom — see lines 320/349).

If pass-through already exists, make NO code change here; record that in the commit message.

- [ ] **Step 3: Run the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all green, no regressions).

- [ ] **Step 4: Commit**

```bash
git add harness/acp_agent.py
git commit -m "feat(acp): emit model plan as ACP plan update

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

(If no code change was needed, commit a one-line note in the plan/spec instead, or skip.)

---

### Task 6: Full-suite verification + manual smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS — full green (the pre-change baseline is ~492 tests; expect that plus the new ones).

- [ ] **Step 2: Manual smoke (optional but recommended)**

Launch the TUI and give the agent a clearly multi-step task. Confirm: the checklist appears below the status line while working, steps tick from `□`→`▣`→`✓`, and the checklist disappears when the turn finishes. A single-step task shows no checklist (status line only). `ctrl+o` still reveals the per-tool list.

- [ ] **Step 3: Commit any smoke-driven fixes** (only if needed).

---

## Self-Review

**Spec coverage:**
- Render the plan update (spec component 1) → Task 1. ✓
- `PlanUpdated`/replace-semantics + `plan` field + `TurnStarted` clear (component 2) → Task 2. ✓ (folded into `ItemReceived` per the spec's resolved wiring — no app.py change.)
- Show TaskTree when plan present (component 3) → Task 3. ✓
- System-prompt instruction (component 4) → Task 4. ✓
- ACP emit / unconditional `session_update` (spec §ACP contract) → Task 5. ✓
- Breaking test `test_render_unknown_returns_none` → handled in Task 1 Step 1. ✓
- `RenderedItem.kind` docstring update → Task 1 Step 3. ✓
- Status mapping pending/in_progress/completed→done → Task 2 Step 4 (`_plan_task_status`). ✓
- Empty plan hides checklist → Task 1 (`entries=()`) + Task 3 (`bool(snap.plan)`). ✓
- Priority ignored, no persistence, no bespoke tool → respected (no task adds them). ✓

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to". Task 5 is conditional by necessity (transport depends on the existing loop shape) but gives the exact code for both branches and an inspection step first. Acceptable.

**Type consistency:** `RenderedItem.entries: tuple[tuple[str,str],...]` defined in Task 1, consumed identically in Task 2. `AgentSnapshot.plan: tuple[TaskItem,...]` defined in Task 2, consumed in Task 3. `show_plan(snap)->bool` defined and consumed in Task 3. `_plan_task_status` defined and used in Task 2. Consistent throughout.
