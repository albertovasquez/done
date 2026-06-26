# TUI Pinned Activity Region Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move tool-call activity out of the transcript scroll into a pinned, transient ActivityRegion above the composer; track all of a turn's tools by id in the reducer; fix transcript spacing and three rendering bugs.

**Architecture:** The pure reducer (`state.py`) gains per-tool tracking (`ToolView.id`, `AgentSnapshot.tools`). A new `ActivityRegion` widget (wrapping the existing `ActivityStatus` + `TaskTree`, plus expandable per-tool `ToolCallRow` detail) becomes the single pinned zone; `app.py` stops mounting tool calls inline. CSS adds transcript spacing and an inline-code background reset.

**Tech Stack:** Python 3.10+, Textual ≥8,<9, pytest. Pure unit tests (reducer/widgets) + Textual `run_test()` pilot tests. No pytest-textual-snapshot.

## Global Constraints

- Always work in the git worktree `worktree-tui-pinned-activity`; never on `main` or the primary checkout (AGENTS.md #1). Implementers: use ABSOLUTE worktree paths for edits and verify `git -C <primary-checkout> status --short` is empty after each task ([[subagent-edits-wrong-checkout]] lesson).
- No edits under `upstream/` (AGENTS.md #4).
- No hardcoded hex outside `harness/tui/theme.py` / `COLORS`. Components read semantic tokens via `[$token]` markup.
- Widgets stay dumb/reactive: they read a snapshot slice; state transitions live ONLY in the reducer (AGENTS.md #7).
- PRESERVE in `app.py on_session_update`: the `gen`/`session_id` freshness guards, the streaming-Markdown answer path (`_stream_message`), the `stream_reset` boundary handling, the decision fold, the chip lines, permission/cancel/clear plumbing.
- Brand voice = restraint: one looping animation (the ActivityStatus glyph), ≤250ms transitions, reduced-motion fallback exists on ActivityStatus.
- Worktree has no `.venv`. Test interpreter (run from worktree root):
  `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/<file> -q`
- Test file header (top of any new test file):
  ```python
  import sys
  sys.path.insert(0, "upstream/src")
  sys.path.insert(0, ".")
  ```
- Commit trailer (AGENTS.md #8):
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- Spec: `docs/superpowers/specs/2026-06-26-tui-pinned-activity-region-design.md`.

---

## File Structure

**New files:**
- `harness/tui/widgets/activity_region.py` — `ActivityRegion(Vertical)`: the pinned, transient zone. Owns the top rule + `ActivityStatus` + collapsed `TaskTree` + (on toggle) per-tool `ToolCallRow` detail. Renders zero-height when idle. `update_from(snapshot)`, `toggle_details()`.

**Modified files:**
- `harness/tui/state.py` — `ToolView` gains `id: str = ""`; `AgentSnapshot` gains `tools: tuple[ToolView, ...] = ()`; reducer tracks tools by id (`tool` item appends to `tools`; `tool_update` updates the matching `ToolView` by id; `TurnStarted` resets `tools=()`).
- `harness/tui/widgets/tool_call_row.py` — gains a body slot (expanded detail) + body tailoring/capping helper; keeps `line_for` (collapsed one-liner).
- `harness/tui/widgets/activity_status.py` — token clause hidden when 0; widget owns the single `…` (reducer labels drop theirs).
- `harness/tui/app.py` — `on_session_update`: `tool`/`tool_update` fold to reducer + refresh `ActivityRegion` only (no `_append`, no `_tool_rows`); `_enter_conversation` mounts one `ActivityRegion`; `_reset_conversation` drops `_tool_rows`; add `ctrl+o` binding → `action_toggle_details`.
- `harness/tui/app.tcss` — `#activity-region` rule; transcript block spacing (margin on `.user-msg` / Markdown); inline-code background reset.
- `harness/tui/styles/components.md` — document `ActivityRegion` (new), `ToolCallRow` re-scope, and the "transcript = messages + responses; tool activity = pinned + transient" rule.
- `tests/test_tui_pilot.py` — invert `test_pilot_tool_call_renders_as_tool_call_row` (tool calls NOT in transcript; in the region); update `test_reset_conversation_clears_tool_rows_and_snapshot` (no `_tool_rows`).

**Sequencing:** Task 1 (reducer per-tool) → Task 2 (ActivityStatus polish) → Task 3 (ToolCallRow body) → Task 4 (ActivityRegion widget) → Task 5 (app.py integration) → Task 6 (CSS spacing + inline-code) → Task 7 (pilot test inversion) → Task 8 (components.md docs).

---

### Task 1: Reducer tracks all of a turn's tools by id

**Files:**
- Modify: `harness/tui/state.py`
- Test: `tests/test_tui_state.py` (extend)

**Interfaces:**
- Produces: `ToolView` gains `id: str = ""`. `AgentSnapshot` gains `tools: tuple[ToolView, ...] = ()`. Reducer: `tool` item appends a `ToolView(id=item.id, ...)` to `tools` and sets `tool` to it; `tool_update` replaces the `ToolView` in `tools` whose `id == item.id` (no-op if none) and updates the matching `TaskItem`; `tool` (live) becomes the updated view; `TurnStarted` resets `tools=()`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_state.py
def test_reducer_tracks_multiple_tools_by_id():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ echo one", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t2", title="$ pytest two", status="pending")))
    a = fs.active
    assert len(a.tools) == 2
    assert a.tools[0].id == "t1" and a.tools[1].id == "t2"
    # update the FIRST tool — must update t1, NOT the latest (t2)
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1", status="completed", body="hi")))
    a = fs.active
    by_id = {tv.id: tv for tv in a.tools}
    assert by_id["t1"].status == ToolStatus.DONE, "t1 should be DONE"
    assert by_id["t2"].status == ToolStatus.PENDING, "t2 must stay PENDING (not clobbered)"


def test_turn_started_resets_tools():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ x", status="pending")))
    assert len(fs.active.tools) == 1
    fs = reduce(fs, TurnStarted())
    assert fs.active.tools == (), "TurnStarted must reset tools"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_tui_state.py::test_reducer_tracks_multiple_tools_by_id -q`
Expected: FAIL (`AttributeError: 'AgentSnapshot' object has no attribute 'tools'`)

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/state.py`:

Add `id` to `ToolView`:
```python
@dataclass(frozen=True)
class ToolView:
    title: str
    status: ToolStatus
    subtype: str
    body: str = ""
    id: str = ""
```

Add `tools` to `AgentSnapshot` (after `tasks`):
```python
    tasks: tuple[TaskItem, ...] = ()
    tools: tuple[ToolView, ...] = ()
```

In `_reduce_agent`, `TurnStarted` branch — add `tools=()`:
```python
    if isinstance(event, TurnStarted):
        return replace(a, state=AgentState.THINKING, activity_label="Thinking",
                       tool=None, decision=None, tasks=(), tools=(), elapsed=0.0)
```
(Note: `activity_label="Thinking"` without the ellipsis — Task 2 owns the `…`. If Task 2 runs first that's fine; set it here too for consistency.)

Replace the `tool` item branch:
```python
        if kind == "tool":
            ts = _tool_status(getattr(item, "status", ""))
            title = getattr(item, "title", "")
            tid = getattr(item, "id", "")
            subtype = infer_subtype(title)
            tool = ToolView(title=title, status=ts, subtype=subtype, id=tid)
            tasks = a.tasks + (TaskItem(label=title, status="in_progress"),)
            tools = a.tools + (tool,)
            return replace(a, state=AgentState.RUNNING_TOOL, tool=tool,
                           tasks=tasks, tools=tools, activity_label=f"Running {subtype}")
```

Replace the `tool_update` item branch (match by id):
```python
        if kind == "tool_update":
            ts = _tool_status(getattr(item, "status", ""))
            uid = getattr(item, "id", "")
            new_tools = tuple(
                replace(tv, status=ts) if tv.id == uid else tv for tv in a.tools
            )
            updated = next((tv for tv in new_tools if tv.id == uid), None)
            new_task_status = _task_status_from_tool(ts)
            new_tasks = tuple(
                replace(t, status=new_task_status)
                if (updated is not None and t.label == updated.title) else t
                for t in a.tasks
            )
            live = updated if updated is not None else a.tool
            return replace(a, tool=live, tools=new_tools, tasks=new_tasks)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_tui_state.py -q`
Expected: PASS (all, including the existing reducer tests — the single-tool path still works because the existing tests use one tool id).

- [ ] **Step 5: Verify primary checkout clean + commit**

```bash
git -C /Users/alberto/Work/quiubo/harness status --short   # must print nothing
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): reducer tracks all of a turn's tools by id

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: ActivityStatus — single ellipsis + hide-tokens-when-0

**Files:**
- Modify: `harness/tui/widgets/activity_status.py`, `harness/tui/state.py` (drop ellipsis from labels)
- Test: `tests/test_tui_widgets.py` (extend)

**Interfaces:**
- Produces: `ActivityStatus.line_for` renders exactly one `…` after the label and OMITS the `· ↓ N tokens` clause when `snap.tokens == 0`. Reducer labels become `"Thinking"`, `"Responding"`, `"Running <subtype>"` (no trailing `…`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_widgets.py
from harness.tui.state import AgentSnapshot, AgentState

def test_activity_status_single_ellipsis():
    w = ActivityStatus()
    snap = AgentSnapshot(id="default", name="agent", state=AgentState.RESPONDING,
                         activity_label="Responding", elapsed=5.0, tokens=0)
    line = w.line_for(snap)
    assert "……" not in line, f"double ellipsis: {line!r}"
    assert "Responding…" in line


def test_activity_status_hides_zero_tokens():
    w = ActivityStatus()
    snap0 = AgentSnapshot(id="default", name="agent", state=AgentState.RESPONDING,
                          activity_label="Responding", elapsed=5.0, tokens=0)
    assert "tokens" not in w.line_for(snap0), "0 tokens must be hidden"
    snapN = AgentSnapshot(id="default", name="agent", state=AgentState.RESPONDING,
                          activity_label="Responding", elapsed=5.0, tokens=1500)
    assert "tokens" in w.line_for(snapN), "nonzero tokens must show"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py::test_activity_status_hides_zero_tokens -q`
Expected: FAIL (the current `line_for` always includes the token clause)

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/widgets/activity_status.py`, replace `line_for`:
```python
    def line_for(self, snap: AgentSnapshot, glyph: str = "◐") -> str:
        if snap.state not in _WORKING:
            return ""
        label = snap.activity_label or "Working"
        meta = _fmt_elapsed(snap.elapsed)
        if snap.tokens > 0:
            meta += f" · ↓ {_fmt_tokens(snap.tokens)} tokens"
        return f"[$accent]{glyph}[/] [$foreground]{label}…[/] [$muted]({meta})[/]"
```

In `harness/tui/state.py`, drop the trailing `…` from the three labels:
- `TurnStarted` → `activity_label="Thinking"`
- `message` item → `activity_label="Responding"`
- (the `tool` item already uses `f"Running {subtype}"` — no `…`, leave it)

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py tests/test_tui_state.py -q`
Expected: PASS (all). If an existing state test asserts `activity_label == "Thinking…"` / `"Responding…"`, update it to the no-ellipsis form.

- [ ] **Step 5: Verify primary clean + commit**

```bash
git -C /Users/alberto/Work/quiubo/harness status --short
git add harness/tui/widgets/activity_status.py harness/tui/state.py tests/test_tui_widgets.py tests/test_tui_state.py
git commit -m "fix(tui): ActivityStatus owns one ellipsis; hide token clause when 0

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: ToolCallRow gains an expandable body

**Files:**
- Modify: `harness/tui/widgets/tool_call_row.py`
- Test: `tests/test_tui_widgets.py` (extend)

**Interfaces:**
- Consumes: `ToolView` (now with `.body`).
- Produces: `ToolCallRow(tool, expanded: bool = False)`. `line_for(tool)` = collapsed one-liner (unchanged). New `detail_for(tool) -> str` = collapsed header line + a tailored, capped body block when `tool.body` is non-empty: read→first 6 lines, generic→first 10 lines (edit/bash distinctions are display niceties — for this task, generic 10-line cap is sufficient; subtype-specific formatting can come later). Module helper `cap_body(body: str, subtype: str) -> str` (pure).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_widgets.py
from harness.tui.state import ToolView, ToolStatus
from harness.tui.widgets.tool_call_row import ToolCallRow, cap_body

def test_cap_body_caps_lines():
    body = "\n".join(f"line{i}" for i in range(20))
    assert cap_body(body, "read").count("\n") <= 6
    assert cap_body(body, "shell").count("\n") <= 10
    assert cap_body("", "shell") == ""


def test_tool_call_row_detail_includes_body():
    tool = ToolView(title="$ cat f.py", status=ToolStatus.DONE, subtype="read",
                    body="alpha\nbeta", id="t1")
    row = ToolCallRow(tool, expanded=True)
    detail = row.detail_for(tool)
    assert "f.py" in detail
    assert "alpha" in detail and "beta" in detail


def test_tool_call_row_collapsed_line_unchanged():
    tool = ToolView(title="$ pytest", status=ToolStatus.ACTIVE, subtype="test", id="t1")
    row = ToolCallRow(tool)
    assert "⚑" in row.line_for(tool) and "pytest" in row.line_for(tool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py::test_cap_body_caps_lines -q`
Expected: FAIL (`ImportError: cannot import name 'cap_body'`)

- [ ] **Step 3: Write minimal implementation**

Replace `harness/tui/widgets/tool_call_row.py`:
```python
"""ToolCallRow — one tool call inside the pinned ActivityRegion: subtype glyph +
title + status chip (collapsed), plus a tailored, capped body when expanded.
Reads a ToolView. Subtype glyph is inferred (display-only). See spec §3."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import ToolView
from harness.tui.tokens import GLYPH
from harness.tui.widgets.status_chip import TOOL_STATUS_TOKEN, TOOL_STATUS_LABEL

_CAP = {"read": 6}        # per-subtype line cap; default below
_DEFAULT_CAP = 10


def cap_body(body: str, subtype: str) -> str:
    """Truncate a tool's output to a per-subtype line cap. Pure/display-only."""
    if not body:
        return ""
    cap = _CAP.get(subtype, _DEFAULT_CAP)
    lines = body.splitlines()
    if len(lines) <= cap:
        return "\n".join(lines)
    return "\n".join(lines[:cap] + [f"… (+{len(lines) - cap} more lines)"])


class ToolCallRow(Static):
    def __init__(self, tool: ToolView, expanded: bool = False) -> None:
        super().__init__(markup=True)
        self._tool = tool
        self._expanded = expanded
        self.update(self.detail_for(tool) if expanded else self.line_for(tool))

    def line_for(self, tool: ToolView) -> str:
        glyph = GLYPH.get(tool.subtype, GLYPH["shell"])
        title = tool.title[2:] if tool.title.startswith("$ ") else tool.title
        token = TOOL_STATUS_TOKEN.get(tool.status, "muted")
        label = TOOL_STATUS_LABEL.get(tool.status, "")
        return (f"[${token}]{glyph}[/] [$foreground]{title}[/]   "
                f"[${token}][b]{label}[/b][/]")

    def detail_for(self, tool: ToolView) -> str:
        head = self.line_for(tool)
        body = cap_body(tool.body, tool.subtype)
        if not body:
            return head
        escaped = body.replace("[", "\\[")
        return f"{head}\n[$code]{escaped}[/]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py -q`
Expected: PASS (all)

- [ ] **Step 5: Verify primary clean + commit**

```bash
git -C /Users/alberto/Work/quiubo/harness status --short
git add harness/tui/widgets/tool_call_row.py tests/test_tui_widgets.py
git commit -m "feat(tui): ToolCallRow expandable body with per-subtype line cap

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: ActivityRegion widget

**Files:**
- Create: `harness/tui/widgets/activity_region.py`
- Test: `tests/test_tui_widgets.py` (extend)

**Interfaces:**
- Consumes: `AgentSnapshot`, `ActivityStatus`, `TaskTree`, `ToolCallRow`.
- Produces: `ActivityRegion(Vertical)` with `update_from(snapshot: AgentSnapshot)` and `toggle_details()`. While working: shows the `ActivityStatus` line + collapsed `TaskTree`; when `_details` is True, shows per-tool `ToolCallRow(expanded=True)` for each `snapshot.tools` instead of the TaskTree. When NOT working (idle/done/failed): renders empty (children cleared / hidden). Exposes `is_idle(snap) -> bool` (pure helper) used by tests.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_widgets.py
import asyncio
from textual.app import App, ComposeResult
from harness.tui.widgets.activity_region import ActivityRegion
from harness.tui.state import AgentSnapshot, AgentState, ToolView, ToolStatus, TaskItem


def _working_snap():
    return AgentSnapshot(
        id="default", name="agent", state=AgentState.RUNNING_TOOL,
        activity_label="Running test", elapsed=4.0, tokens=0,
        tasks=(TaskItem(label="$ pytest", status="in_progress"),),
        tools=(ToolView(title="$ pytest", status=ToolStatus.ACTIVE, subtype="test",
                        body="ran 3 tests", id="t1"),),
        tool=ToolView(title="$ pytest", status=ToolStatus.ACTIVE, subtype="test", id="t1"),
    )


def test_activity_region_idle_helper():
    region = ActivityRegion()
    idle = AgentSnapshot(id="default", name="agent", state=AgentState.IDLE)
    done = AgentSnapshot(id="default", name="agent", state=AgentState.DONE)
    assert region.is_idle(idle) and region.is_idle(done)
    assert not region.is_idle(_working_snap())


def test_activity_region_mounts_and_shows_tool_when_working():
    class Host(App):
        def compose(self) -> ComposeResult:
            yield ActivityRegion(id="activity-region")
    async def go():
        async with Host().run_test() as pilot:
            region = pilot.app.query_one("#activity-region", ActivityRegion)
            region.update_from(_working_snap())
            await pilot.pause()
            # collapsed: the TaskTree checklist is present with the tool label
            from harness.tui.widgets.task_tree import TaskTree
            assert region.query(TaskTree), "TaskTree should be present while working"
            # toggle to detail: ToolCallRow(s) appear
            region.toggle_details()
            region.update_from(_working_snap())
            await pilot.pause()
            from harness.tui.widgets.tool_call_row import ToolCallRow
            assert region.query(ToolCallRow), "ToolCallRow should appear when expanded"
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py::test_activity_region_idle_helper -q`
Expected: FAIL (`ModuleNotFoundError: harness.tui.widgets.activity_region`)

- [ ] **Step 3: Write minimal implementation**

Create `harness/tui/widgets/activity_region.py`:
```python
"""ActivityRegion — the pinned, transient zone above the composer that shows what
the agent is doing RIGHT NOW. Tool calls live here, NOT in the transcript scroll.
Compact while working (status line + task checklist); ctrl+o expands to per-tool
detail; renders empty when idle/terminal. Reads an AgentSnapshot. See spec §3."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from harness.tui.state import AgentSnapshot, AgentState
from harness.tui.widgets.activity_status import ActivityStatus
from harness.tui.widgets.task_tree import TaskTree
from harness.tui.widgets.tool_call_row import ToolCallRow

_WORKING = {AgentState.THINKING, AgentState.RESPONDING, AgentState.RUNNING_TOOL,
            AgentState.AWAITING_PERMISSION, AgentState.AWAITING_DECISION}


class ActivityRegion(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._details = False
        self._snap: AgentSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="ar-rule", markup=True)          # top hairline
        yield ActivityStatus(id="ar-status")
        yield Vertical(id="ar-body")                          # TaskTree or ToolCallRows

    def is_idle(self, snap: AgentSnapshot) -> bool:
        return snap is None or snap.state not in _WORKING

    def toggle_details(self) -> None:
        self._details = not self._details
        if self._snap is not None:
            self.update_from(self._snap)

    def update_from(self, snap: AgentSnapshot) -> None:
        self._snap = snap
        idle = self.is_idle(snap)
        self.display = not idle                               # zero-height when idle
        self.query_one("#ar-status", ActivityStatus).update_from(snap)
        self.query_one("#ar-rule", Static).update(
            "" if idle else "[$muted]" + "─" * 40 + "[/]")
        body = self.query_one("#ar-body", Vertical)
        body.remove_children()
        if idle:
            return
        if self._details and snap.tools:
            for tv in snap.tools:
                body.mount(ToolCallRow(tv, expanded=True))
        else:
            tree = TaskTree()
            body.mount(tree)
            tree.update_tasks(snap.tasks)
```

NOTE on `body.mount(...)` inside `update_from`: Textual's `mount` is async-scheduled; for the test, calling `update_from` then `await pilot.pause()` lets the mounts settle. If `remove_children()`/`mount` ordering races in practice, switch to `body.mount_all([...])` after `await body.remove_children()` in an async refresh — but the simple form above is sufficient for the pinned region (low widget count, refreshed per event).

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py -q`
Expected: PASS (all). If the mount-timing in the pilot test flakes, add an extra `await pilot.pause()` after `update_from`.

- [ ] **Step 5: Verify primary clean + commit**

```bash
git -C /Users/alberto/Work/quiubo/harness status --short
git add harness/tui/widgets/activity_region.py tests/test_tui_widgets.py
git commit -m "feat(tui): ActivityRegion pinned transient zone (collapsed/expanded/idle)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Wire ActivityRegion into app.py; remove inline tool rendering

**Files:**
- Modify: `harness/tui/app.py`
- Test: covered by Task 7 pilot tests + the full suite here

**Interfaces:**
- Consumes: `ActivityRegion`. Removes `_tool_rows`. Adds `action_toggle_details`.

- [ ] **Step 1: Make the edits**

1. Imports (`harness/tui/app.py` ~line 41-47): add
   `from harness.tui.widgets.activity_region import ActivityRegion`. Keep the
   `ActivityStatus`/`TaskTree`/`ToolCallRow` imports only if still referenced; after
   this task `ActivityStatus`/`TaskTree` are used only inside `ActivityRegion`, so
   remove their imports from app.py if unused (leave `ToolCallRow` import removed too
   — it's only used inside the region now). Run the suite to confirm no NameError.

2. BINDINGS (~line 75): add the toggle:
   ```python
   BINDINGS = [("escape", "cancel", "Cancel turn"),
               ("ctrl+o", "toggle_details", "Tool details")]
   ```

3. `__init__` (~line 103): remove `self._tool_rows: dict[...] = {}`.

4. `_apply` (~line 236): replace the two `query_one("#activity"...)` /
   `query_one("#tasktree"...)` refreshes with one:
   ```python
   def _apply(self, event) -> None:
       self._snapshot = reduce(self._snapshot, event)
       a = self._snapshot.active
       if a is None:
           return
       try:
           self.query_one("#activity-region", ActivityRegion).update_from(a)
       except Exception:
           pass
   ```

5. `_tick_elapsed` (~line 251): change its final refresh to the region:
   ```python
       try:
           self.query_one("#activity-region", ActivityRegion).update_from(self._snapshot.active)
       except Exception:
           pass
   ```
   (Replace the existing `query_one("#activity", ActivityStatus)` refresh.)

6. `_enter_conversation` (~line 480): replace the two mounts
   ```python
       await self.mount(ActivityStatus(id="activity"), before="#statusbar")
       await self.mount(TaskTree(id="tasktree"), before="#statusbar")
   ```
   with one:
   ```python
       await self.mount(ActivityRegion(id="activity-region"), before="#composer")
   ```
   (Mount it before `#composer` so it sits directly above the composer; `#composer`
   is mounted next, also `before="#statusbar"`. Order: transcript, region, composer.)

   Adjust: mount the region AFTER the composer is created? No — mount transcript, then
   composer, then region `before="#composer"`. Simplest: keep transcript mount,
   create+mount composer, then `await self.mount(ActivityRegion(id="activity-region"), before="#composer")`.

7. `_reset_conversation` (~line 505): remove `self._tool_rows = {}`; change the
   widget refresh to the region:
   ```python
       try:
           self.query_one("#activity-region", ActivityRegion).update_from(self._snapshot.active)
       except Exception:
           pass
   ```
   (Remove the old `#activity` ActivityStatus refresh.)

8. `on_session_update` `tool` branch (~line 703): replace the inline-mount block
   with NOTHING beyond the reducer fold — the `_apply(ItemReceived(item))` earlier in
   the method already updates the snapshot, and `_apply` refreshes the region. So the
   `tool` and `tool_update` branches collapse to:
   ```python
       elif item.kind == "tool":
           self._end_stream(boundary=True)  # finalize the current answer block
           # tool activity is shown in the pinned ActivityRegion (refreshed by _apply),
           # NOT inline in the transcript.
       elif item.kind == "tool_update":
           pass  # handled by the reducer fold + ActivityRegion refresh
   ```
   Keep the `message`, `thought`, `user` branches unchanged. Remove the `_GLYPH` /
   `_status_hex` fallback lines that were only used by the removed inline rendering
   IF they're now unused (check: `_status_hex` may be used elsewhere — only remove
   `_GLYPH` if unreferenced).

9. Add the action:
   ```python
   def action_toggle_details(self) -> None:
       try:
           self.query_one("#activity-region", ActivityRegion).toggle_details()
       except Exception:
           pass
   ```

- [ ] **Step 2: Run the full suite (expect the inline-toolrow pilot test to fail — Task 7 fixes it)**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: the two PR-30 tests that assert inline `ToolCallRow` / `_tool_rows`
(`test_pilot_tool_call_renders_as_tool_call_row`, `test_reset_conversation_clears_tool_rows_and_snapshot`)
FAIL; everything else PASSES. This is expected — Task 7 inverts those tests. If
ANY OTHER test fails (e.g. a streaming or guard test), fix the integration before
proceeding.

- [ ] **Step 3: Verify primary clean + commit**

```bash
git -C /Users/alberto/Work/quiubo/harness status --short
git add harness/tui/app.py
git commit -m "feat(tui): pinned ActivityRegion replaces inline tool rendering; ctrl+o details

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: CSS — region styling, transcript spacing, inline-code background

**Files:**
- Modify: `harness/tui/app.tcss`
- Test: pilot (Task 7) + visual; assert via the suite that nothing breaks

**Interfaces:** none (CSS only).

- [ ] **Step 1: Make the edits**

In `harness/tui/app.tcss`:

1. Replace the old `#activity` / `#tasktree` rules (lines ~118-120) with region rules:
   ```css
   /* ---- pinned activity region (above the composer) ---- */
   #activity-region { height: auto; padding: 0 2; margin: 0 2; }
   #activity-region.-hidden { display: none; }
   #ar-rule { height: 1; color: $muted; }
   #ar-status { height: 1; color: $accent; }
   #ar-body { height: auto; color: $foreground; }
   ```

2. Inline-code background reset (line ~54): change
   ```css
   #transcript Markdown .code_inline { color: $code; }
   ```
   to
   ```css
   #transcript Markdown .code_inline { color: $code; background: $background; }
   ```

3. Transcript block spacing — give the user message and answer breathing room. Add
   to `.user-msg` (it already exists ~line 60) a top margin, and give transcript
   Markdown a bottom margin:
   ```css
   .user-msg { margin: 1 0 0 0; }          /* blank line above each user message */
   #transcript Markdown { margin: 0 0 1 0; }   /* blank line below each answer */
   ```
   (Merge these into the existing `.user-msg` and `#transcript Markdown` blocks
   rather than duplicating selectors — adjust the existing `margin: 0` on the
   Markdown rule to `margin: 0 0 1 0`.)

- [ ] **Step 2: Run the suite (no regressions beyond Task 5's expected two)**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: same two known failures from Task 5, nothing new.

- [ ] **Step 3: Verify primary clean + commit**

```bash
git -C /Users/alberto/Work/quiubo/harness status --short
git add harness/tui/app.tcss
git commit -m "style(tui): activity-region CSS; transcript block spacing; inline-code bg reset

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Invert the pilot tests (tools NOT inline; region shows them)

**Files:**
- Modify: `tests/test_tui_pilot.py`
- Test: itself

**Interfaces:** none.

- [ ] **Step 1: Replace the two outdated tests**

In `tests/test_tui_pilot.py`:

Replace `test_pilot_tool_call_renders_as_tool_call_row` (the one asserting an inline
`ToolCallRow` in `#transcript`) with the inverse:
```python
def test_pilot_tool_call_is_not_in_transcript_but_in_region():
    """A ToolCallStart update does NOT mount a ToolCallRow in the transcript; the
    pinned ActivityRegion reflects the tool instead."""
    from harness.tui.widgets.tool_call_row import ToolCallRow
    from harness.tui.widgets.activity_region import ActivityRegion
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            tool_start = acp.start_tool_call("tc-test", title="$ echo hello",
                                             status="in_progress")
            app.on_session_update(SessionUpdate(tool_start))
            await pilot.pause()
            scroll = app.query_one("#transcript", VerticalScroll)
            assert not [w for w in scroll.children if isinstance(w, ToolCallRow)], \
                "tool calls must NOT be inline in the transcript"
            # the pinned region tracks the tool in its snapshot
            assert any(tv.id == "tc-test" for tv in app._snapshot.active.tools), \
                "the region's snapshot should track the tool"
            assert app.query_one("#activity-region", ActivityRegion).display is True, \
                "region should be visible while a tool runs"
    asyncio.run(go())
```

Replace `test_reset_conversation_clears_tool_rows_and_snapshot` — drop the
`_tool_rows` references (the dict is gone); keep the snapshot reset assertion:
```python
def test_reset_conversation_resets_snapshot():
    from harness.tui.state import AgentState
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            await app._reset_conversation()
            await pilot.pause()
            assert app._snapshot.active.state == AgentState.IDLE, \
                "snapshot should be reset to IDLE after _reset_conversation"
    asyncio.run(go())
```

If line 15's `from harness.tui.widgets.tool_call_row import ToolCallRow` at module
top is now only used inside the new test, leave it (harmless) or move it into the
test; either is fine.

- [ ] **Step 2: Run the full suite — now fully green**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all). No remaining failures.

- [ ] **Step 3: Verify primary clean + commit**

```bash
git -C /Users/alberto/Work/quiubo/harness status --short
git add tests/test_tui_pilot.py
git commit -m "test(tui): tool calls are in the pinned region, not the transcript

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Document the change in components.md

**Files:**
- Modify: `harness/tui/styles/components.md`
- Test: none (docs)

- [ ] **Step 1: Update the catalog**

In `harness/tui/styles/components.md`:
- Add `ActivityRegion` under section C (work-in-progress): the pinned, transient zone
  above the composer; compact while working, `ctrl+o` expands to per-tool detail,
  empty when idle; reads `AgentSnapshot`. Note it OWNS `ActivityStatus` + `TaskTree`
  + per-tool `ToolCallRow`.
- Re-scope `ToolCallRow`: no longer a transcript widget — it is the expanded-detail
  row INSIDE `ActivityRegion`. `line_for` = collapsed; `detail_for` = header + capped
  body; `cap_body` per-subtype cap.
- Add a principle near the top: **"Transcript = user messages + agent responses
  only. Tool-call activity is pinned + transient (ActivityRegion), never inline."**
- Note the snapshot now carries `tools: tuple[ToolView,...]` (all of a turn's tools
  by id) alongside `tool` (the live one).

- [ ] **Step 2: Verify primary clean + commit**

```bash
git -C /Users/alberto/Work/quiubo/harness status --short
git add harness/tui/styles/components.md
git commit -m "docs(tui): components.md — ActivityRegion + pinned-not-inline rule

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after Task 8)

- [ ] Full suite from worktree root: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/ -q` → all green.
- [ ] Manual smoke (optional): `./run.sh --model mock`; confirm tool activity shows in the pinned region above the composer (not inline), `ctrl+o` toggles detail, the region clears when the turn ends, and inline code in answers has no grey block.
- [ ] No hardcoded hex: `grep -rnE '#[0-9A-Fa-f]{6}' harness/tui/widgets/activity_region.py harness/tui/widgets/tool_call_row.py` → no matches.
- [ ] Primary checkout clean: `git -C /Users/alberto/Work/quiubo/harness status --short` → empty.

## Self-Review notes (coverage map)

- Spec §2/D1 (pinned not inline) → Tasks 4, 5. D2 (clears to idle) → Task 4 (`is_idle`/`display`), Task 5 (TurnEnded refresh). D3 (expandable) → Tasks 3, 4, 5 (ctrl+o). D4 (spacing) → Task 6. D5 (tools by id) → Task 1.
- Spec §5 polish: inline-code bg → Task 6; double ellipsis → Task 2; 0 tokens → Task 2.
- Spec §3 ToolCallRow re-scope → Task 3; ActivityRegion → Task 4. §6 app integration → Task 5. §7 tests → Tasks 1–4, 7. Docs → Task 8.
- Preserved (guards, streaming, stream_reset, decision fold, permission): Task 5 explicitly keeps them; the full suite (Task 7) re-verifies via existing guard/streaming tests.
