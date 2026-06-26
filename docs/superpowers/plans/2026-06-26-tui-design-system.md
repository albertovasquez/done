# TUI Design System Implementation Plan (Phases 1–3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the single-agent on-ramp of the TUI design system — design tokens, a pure agent-state reducer, and the headline activity / task-tree / tool-row / decision-prompt widgets — on top of the existing Textual client without rewriting it.

**Architecture:** A new pure, Textual-free module `harness/tui/state.py` folds the existing `RenderedItem`s + meta into an immutable `FleetSnapshot`. Dumb reactive widgets read snapshot slices. `app.on_session_update` keeps its `gen`/`session_id` freshness guards and routes events through `reduce()`. The streaming-Markdown answer path (`_stream_message`) is untouched.

**Tech Stack:** Python 3.10+, Textual ≥8,<9, Rich (via Textual), pytest. Tests are pure unit tests (reducer) + Textual `run_test()` pilot tests (widgets). No `pytest-textual-snapshot` (not a dependency).

## Global Constraints

- Always work in the git worktree on its own branch; never on `main` (AGENTS.md #1). This plan's worktree is `worktree-tui-design-system`.
- No edits under `upstream/` (AGENTS.md #4).
- No hardcoded hex outside `harness/tui/theme.py` / `COLORS` (spec M2). Components read semantic tokens.
- Status is carried by **color + glyph + weight together** (spec §4.1).
- Brand voice = restraint: motion signals a state change; exactly one looping animation on screen (the active glyph); transitions ≤250ms ease-out; reduced-motion + monochrome fallbacks (spec H4).
- Engine-truthful state model. `subtype` is inferred for glyphs only, neutral `shell` fallback. `awaiting_decision` is recognized from a `field_meta["harness"]` chip (spec §5.1).
- Tests run from the worktree root: `.venv/bin/python -m pytest tests/ -q` — but the worktree has no `.venv`; use the **portable interpreter** the test files already use. Run with the repo's interpreter, e.g. `python -m pytest tests/<file> -q` from the worktree root (the test files do `sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")`).
- Test file header (copy verbatim at the top of every new test file):
  ```python
  import sys
  sys.path.insert(0, "upstream/src")
  sys.path.insert(0, ".")
  ```
- Commit message trailer (AGENTS.md #8):
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- Brand palette (spec §4.2): accent `#286CE9`, fg `#E3E3E3`, slate/muted `#8690A3`, bg `#0A1524`, surface `#16243A`, error `#E02F07`; product status: done `#7ee787`, scheduled/attention `#e3b341`; derived: muted-deep `#5B6577`, code `#9DB8E8`.

---

## File Structure

**New files:**
- `harness/tui/tokens.py` — pure constants: glyph map + status-token names. No Textual. (Color values stay in `theme.py`; this holds glyphs + token-name helpers so widgets and the reducer share one vocabulary.)
- `harness/tui/state.py` — pure: `AgentState`, `ToolStatus` enums; `ToolView`, `TaskItem`, `ScheduleView`, `DecisionView`, `AgentSnapshot`, `FleetSnapshot` dataclasses; `infer_subtype()`; `reduce()`; `initial_snapshot()`.
- `harness/tui/widgets/status_chip.py` — `StatusChip`, `StateDot`, `ActivityGlyph`.
- `harness/tui/widgets/activity_status.py` — `ActivityStatus`.
- `harness/tui/widgets/task_tree.py` — `TaskTree`.
- `harness/tui/widgets/tool_call_row.py` — `ToolCallRow`.
- `harness/tui/widgets/decision_prompt.py` — `DecisionPrompt` (inline target; modal escalation reuses `SelectModal`).
- `tests/test_tui_tokens.py`, `tests/test_tui_state.py`, `tests/test_tui_widgets.py` — new tests.

**Modified files:**
- `harness/tui/theme.py` — add status/product tokens to `variables` + `STATUS_COLOR` keys; re-document green/amber as sanctioned product tokens.
- `harness/tui/messages.py` — add `FleetUpdated` message.
- `harness/tui/app.py` — route `on_session_update` through `reduce()`; replace `_show_working` / flat tool lines with the new widgets; mount an `ActivityStatus` + `TaskTree` region.
- `harness/tui/app.tcss` — add component CSS classes.
- `harness/tui/styles/components.md` — already written; touch only if a component's interface changes.

**Sequencing:** Phase 1 = Tasks 1–2 (tokens). Phase 2 = Tasks 3–7 (state model + reducer). Phase 3 = Tasks 8–13 (widgets + app integration).

---

## PHASE 1 — Tokens & style-guide foundation

### Task 1: Glyph + status-token vocabulary (`tokens.py`)

**Files:**
- Create: `harness/tui/tokens.py`
- Test: `tests/test_tui_tokens.py`

**Interfaces:**
- Produces:
  - `GLYPH: dict[str, str]` — keys: `idle, active, responding, tool, done, failed, scheduled, awaiting, edit, test, read, shell, search`.
  - `STATUS_LABEL: dict[str, str]` — maps an `AgentState`/`ToolStatus` value name to an uppercase chip label, e.g. `"running" -> "RUNNING"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_tokens.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.tokens import GLYPH, STATUS_LABEL


def test_glyph_has_all_state_and_subtype_keys():
    for key in ("idle", "active", "responding", "tool", "done", "failed",
                "scheduled", "awaiting", "edit", "test", "read", "shell", "search"):
        assert key in GLYPH, f"missing glyph: {key}"
        assert GLYPH[key], f"empty glyph: {key}"


def test_status_label_is_uppercase():
    assert STATUS_LABEL["running"] == "RUNNING"
    assert STATUS_LABEL["completed"] == "COMPLETED"
    assert STATUS_LABEL["scheduled"] == "SCHEDULED"
    assert STATUS_LABEL["failed"] == "FAILED"
    assert STATUS_LABEL["queued"] == "QUEUED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_tokens.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.tui.tokens'`

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tui/tokens.py
"""Shared, pure display vocabulary for the TUI design system: the glyph map and
status-chip labels. No Textual, no color values (colors live in theme.py). The
reducer and the widgets both import these so the iconography stays in one place.
See docs/superpowers/specs/2026-06-26-tui-design-system-design.md §4.3."""

from __future__ import annotations

GLYPH: dict[str, str] = {
    # state dots
    "idle": "•",
    "active": "◐",
    "responding": "▌",
    "tool": "›",
    "done": "✓",
    "failed": "✗",
    "scheduled": "⏱",
    "awaiting": "?",
    # tool subtypes (glyph-only, inferred)
    "edit": "✎",
    "test": "⚑",
    "read": "◇",
    "shell": "$",
    "search": "⌕",
}

STATUS_LABEL: dict[str, str] = {
    "idle": "IDLE",
    "thinking": "THINKING",
    "responding": "RESPONDING",
    "running": "RUNNING",
    "queued": "QUEUED",
    "scheduled": "SCHEDULED",
    "completed": "COMPLETED",
    "done": "COMPLETED",
    "failed": "FAILED",
    "awaiting": "AWAITING",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_tokens.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add harness/tui/tokens.py tests/test_tui_tokens.py
git commit -m "feat(tui): glyph + status-label vocabulary (design-system tokens)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Status color tokens in the theme

**Files:**
- Modify: `harness/tui/theme.py`
- Test: `tests/test_tui_tokens.py` (extend)

**Interfaces:**
- Produces: `COLORS` gains `"scheduled"`; `STATUS_COLOR` gains `"scheduled"`; `HARNESS_THEME.variables` gains `"scheduled"` and `"muted-deep"` (already had `muted`). All other keys unchanged.

- [ ] **Step 1: Write the failing test (extend the tokens test file)**

```python
# append to tests/test_tui_tokens.py
def test_theme_has_product_status_tokens():
    from harness.tui.theme import COLORS, STATUS_COLOR, HARNESS_THEME
    # green/amber are sanctioned product-status tokens (spec §4.1)
    assert COLORS["success"] == "#7ee787"
    assert COLORS["scheduled"] == "#e3b341"
    assert STATUS_COLOR["scheduled"] == "#e3b341"
    assert HARNESS_THEME.variables["scheduled"] == "#e3b341"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_tokens.py::test_theme_has_product_status_tokens -q`
Expected: FAIL with `KeyError: 'scheduled'`

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/theme.py`, in `HARNESS_THEME` `variables` dict, add after `"accent-bar": "#286CE9",`:

```python
        "scheduled": "#e3b341",      # product-status: cron/scheduled/attention (sanctioned, spec §4.1)
```

In the `COLORS` dict, add after `"warning": "#e3b341",`:

```python
    "scheduled": "#e3b341",
```

In the `STATUS_COLOR` dict, add a line:

```python
    "scheduled": COLORS["scheduled"],
```

Also update the module docstring's note about green/amber: change "kept for legibility" framing to "adopted as sanctioned product-status tokens for product UI (go / caution / future semantics) — see the TUI design-system spec §4.1."

- [ ] **Step 4: Run the full tokens + theme-touching tests**

Run: `python -m pytest tests/test_tui_tokens.py tests/test_tui_header.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add harness/tui/theme.py tests/test_tui_tokens.py
git commit -m "feat(tui): adopt green/amber as sanctioned product-status tokens

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PHASE 2 — The presentation layer (pure reducer)

### Task 3: State enums + value types (`state.py` part 1)

**Files:**
- Create: `harness/tui/state.py`
- Test: `tests/test_tui_state.py`

**Interfaces:**
- Produces:
  - `class AgentState(str, Enum)` with members `IDLE, THINKING, RESPONDING, RUNNING_TOOL, AWAITING_PERMISSION, AWAITING_DECISION, SCHEDULED, DONE, FAILED` (values are lower-case strings: `"idle"`, etc.).
  - `class ToolStatus(str, Enum)` with `PENDING, ACTIVE, DONE, FAILED` (values `"pending","active","done","failed"`).
  - `@dataclass(frozen=True) ToolView(title: str, status: ToolStatus, subtype: str, body: str = "")`.
  - `@dataclass(frozen=True) TaskItem(label: str, status: str)` — status in `{"pending","in_progress","done","failed"}`.
  - `@dataclass(frozen=True) ScheduleView(label: str, when: str)`.
  - `@dataclass(frozen=True) DecisionView(question: str, options: tuple[tuple[str, str], ...])` — each option `(title, rationale)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_state.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.state import (
    AgentState, ToolStatus, ToolView, TaskItem, ScheduleView, DecisionView,
)


def test_agent_state_values():
    assert AgentState.IDLE.value == "idle"
    assert AgentState.RUNNING_TOOL.value == "running_tool"
    assert AgentState.AWAITING_DECISION.value == "awaiting_decision"


def test_tool_status_values():
    assert ToolStatus.PENDING.value == "pending"
    assert ToolStatus.DONE.value == "done"


def test_value_types_are_frozen():
    tv = ToolView(title="$ ls", status=ToolStatus.ACTIVE, subtype="shell")
    assert tv.body == ""
    dv = DecisionView(question="q?", options=(("a", "because"),))
    assert dv.options[0] == ("a", "because")
    ti = TaskItem(label="do x", status="pending")
    sv = ScheduleView(label="nightly", when="in 2d")
    assert (ti.label, sv.when) == ("do x", "in 2d")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_state.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.tui.state'`

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tui/state.py
"""Pure presentation model for the TUI design system. Folds the existing
RenderedItem stream (render.py) + harness meta into an immutable FleetSnapshot
that dumb, reactive widgets read. No Textual, no async — exhaustively unit-
testable like render.py. See the TUI design-system spec §5."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    RESPONDING = "responding"
    RUNNING_TOOL = "running_tool"
    AWAITING_PERMISSION = "awaiting_permission"
    AWAITING_DECISION = "awaiting_decision"
    SCHEDULED = "scheduled"
    DONE = "done"
    FAILED = "failed"


class ToolStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class ToolView:
    title: str
    status: ToolStatus
    subtype: str
    body: str = ""


@dataclass(frozen=True)
class TaskItem:
    label: str
    status: str          # pending | in_progress | done | failed


@dataclass(frozen=True)
class ScheduleView:
    label: str
    when: str


@dataclass(frozen=True)
class DecisionView:
    question: str
    options: tuple[tuple[str, str], ...]   # (title, rationale)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_state.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): agent-state enums + snapshot value types

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Subtype inference

**Files:**
- Modify: `harness/tui/state.py`
- Test: `tests/test_tui_state.py` (extend)

**Interfaces:**
- Produces: `def infer_subtype(command: str) -> str` — returns one of `edit|test|read|search|shell`; `shell` is the fallback.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_state.py
from harness.tui.state import infer_subtype


def test_infer_subtype():
    assert infer_subtype("pytest tests/ -q") == "test"
    assert infer_subtype("python -m pytest x") == "test"
    assert infer_subtype("sed -i 's/a/b/' f.py") == "edit"
    assert infer_subtype("apply_patch <<EOF") == "edit"
    assert infer_subtype("cat README.md") == "read"
    assert infer_subtype("grep -r foo .") == "search"
    assert infer_subtype("rg foo") == "search"
    assert infer_subtype("echo hello") == "shell"
    assert infer_subtype("") == "shell"
    assert infer_subtype("$ pytest") == "test"   # leading "$ " stripped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_state.py::test_infer_subtype -q`
Expected: FAIL with `ImportError: cannot import name 'infer_subtype'`

- [ ] **Step 3: Write minimal implementation**

Append to `harness/tui/state.py`:

```python
def infer_subtype(command: str) -> str:
    """Guess a tool-call subtype from the command string, for glyph/label ONLY.
    Display concern; never asked of the engine. Neutral 'shell' fallback."""
    c = command.strip()
    if c.startswith("$ "):
        c = c[2:].strip()
    low = c.lower()
    if "pytest" in low or low.startswith("test ") or " test" in low and "pytest" in low:
        return "test"
    if any(k in low for k in ("pytest",)):
        return "test"
    if any(low.startswith(k) or f" {k} " in f" {low} " for k in ("sed", "apply_patch", "patch")):
        return "edit"
    if any(low.startswith(k) for k in ("grep", "rg", "find", "ag")):
        return "search"
    if any(low.startswith(k) for k in ("cat", "head", "tail", "less", "bat")):
        return "read"
    return "shell"
```

Note: keep the test-detection simple — `if "pytest" in low: return "test"` covers the cases above. Simplify the function to:

```python
def infer_subtype(command: str) -> str:
    """Guess a tool-call subtype from the command string, for glyph/label ONLY.
    Display concern; never asked of the engine. Neutral 'shell' fallback."""
    c = command.strip()
    if c.startswith("$ "):
        c = c[2:].strip()
    low = c.lower()
    first = low.split()[0] if low.split() else ""
    if "pytest" in low or first == "test":
        return "test"
    if first in ("sed", "apply_patch", "patch") or "apply_patch" in low:
        return "edit"
    if first in ("grep", "rg", "find", "ag"):
        return "search"
    if first in ("cat", "head", "tail", "less", "bat"):
        return "read"
    return "shell"
```

(Use this second, simpler version.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_state.py::test_infer_subtype -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): infer tool-call subtype for glyphs (shell fallback)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Snapshot dataclasses + initial snapshot

**Files:**
- Modify: `harness/tui/state.py`
- Test: `tests/test_tui_state.py` (extend)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) AgentSnapshot` with fields: `id: str`, `name: str`, `state: AgentState = AgentState.IDLE`, `tool: ToolView | None = None`, `activity_label: str = ""`, `elapsed: float = 0.0`, `tokens: int = 0`, `tasks: tuple[TaskItem, ...] = ()`, `schedule: ScheduleView | None = None`, `decision: DecisionView | None = None`.
  - `@dataclass(frozen=True) FleetSnapshot` with fields: `agents: tuple[AgentSnapshot, ...]`, `active_id: str`. Property `active -> AgentSnapshot | None`.
  - `def initial_snapshot(agent_id: str = "default", name: str = "agent") -> FleetSnapshot`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_state.py
from harness.tui.state import AgentSnapshot, FleetSnapshot, initial_snapshot


def test_initial_snapshot_one_idle_agent():
    fs = initial_snapshot()
    assert len(fs.agents) == 1
    a = fs.active
    assert a is not None
    assert a.id == "default"
    assert a.state == AgentState.IDLE
    assert a.elapsed == 0.0 and a.tokens == 0 and a.tasks == ()


def test_fleet_active_returns_none_when_missing():
    fs = FleetSnapshot(agents=(), active_id="nope")
    assert fs.active is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_state.py::test_initial_snapshot_one_idle_agent -q`
Expected: FAIL with `ImportError: cannot import name 'AgentSnapshot'`

- [ ] **Step 3: Write minimal implementation**

Append to `harness/tui/state.py` (after the value types, before `infer_subtype` is fine):

```python
@dataclass(frozen=True)
class AgentSnapshot:
    id: str
    name: str
    state: AgentState = AgentState.IDLE
    tool: ToolView | None = None
    activity_label: str = ""
    elapsed: float = 0.0
    tokens: int = 0
    tasks: tuple[TaskItem, ...] = ()
    schedule: ScheduleView | None = None
    decision: DecisionView | None = None


@dataclass(frozen=True)
class FleetSnapshot:
    agents: tuple[AgentSnapshot, ...]
    active_id: str

    @property
    def active(self) -> AgentSnapshot | None:
        return next((a for a in self.agents if a.id == self.active_id), None)


def initial_snapshot(agent_id: str = "default", name: str = "agent") -> FleetSnapshot:
    return FleetSnapshot(agents=(AgentSnapshot(id=agent_id, name=name),),
                         active_id=agent_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_state.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): AgentSnapshot / FleetSnapshot + initial_snapshot

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The reducer — events & core transitions

**Files:**
- Modify: `harness/tui/state.py`
- Test: `tests/test_tui_state.py` (extend)

**Interfaces:**
- Consumes: `RenderedItem` from `harness.tui.render` (fields: `kind, text, id, title, status, body`).
- Produces:
  - Event constructors (plain frozen dataclasses) in `state.py`:
    - `TurnStarted()` — user sent a prompt.
    - `TurnEnded(ok: bool = True)` — prompt() returned (`ok=False` → FAILED).
    - `ItemReceived(item)` — a `RenderedItem`.
    - `TokensUpdated(total: int)`.
    - `PermissionOpened()` / `PermissionClosed()`.
  - `def reduce(snapshot: FleetSnapshot, event) -> FleetSnapshot` — pure; updates the **active** agent only (single-agent today).
  - Transition rules (active agent):
    - `TurnStarted` → state `THINKING`, `activity_label="Thinking…"`, `tool=None`, `decision=None`, `tasks=()`, `elapsed=0`.
    - `ItemReceived(kind="message")` → state `RESPONDING`, `activity_label="Responding…"`.
    - `ItemReceived(kind="tool")` → state `RUNNING_TOOL`; set `tool=ToolView(title, status mapped, subtype=infer_subtype(title))`; append a `TaskItem(label=title, status="in_progress")`; `activity_label="Running " + subtype`.
    - `ItemReceived(kind="tool_update")` → update `tool.status` + the matching `TaskItem.status` (`completed`→`done`, `failed`→`failed`, else `in_progress`).
    - `TokensUpdated(n)` → `tokens=n`.
    - `PermissionOpened` → state `AWAITING_PERMISSION`. `PermissionClosed` → back to `RUNNING_TOOL` if a tool is live else `RESPONDING`.
    - `TurnEnded(ok)` → state `DONE` if ok else `FAILED`; `tool=None`, `activity_label=""`.
  - Status string → `ToolStatus`: `"pending"→PENDING`, `"in_progress"/"active"→ACTIVE`, `"completed"→DONE`, `"failed"→FAILED`. Stringified-enum forms (`"ToolCallStatus.failed"`) handled by taking the part after the last `.`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_state.py
from harness.tui.render import RenderedItem
from harness.tui.state import (
    reduce, TurnStarted, TurnEnded, ItemReceived, TokensUpdated,
    PermissionOpened, PermissionClosed,
)


def _active(fs):
    return fs.active


def test_turn_started_goes_thinking():
    fs = reduce(initial_snapshot(), TurnStarted())
    assert _active(fs).state == AgentState.THINKING


def test_message_item_goes_responding():
    fs = initial_snapshot()
    fs = reduce(fs, TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="message", text="hi")))
    assert _active(fs).state == AgentState.RESPONDING


def test_tool_item_sets_tool_and_task():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ pytest tests/", status="pending")))
    a = _active(fs)
    assert a.state == AgentState.RUNNING_TOOL
    assert a.tool is not None
    assert a.tool.subtype == "test"
    assert a.tool.status == ToolStatus.PENDING
    assert len(a.tasks) == 1 and a.tasks[0].status == "in_progress"


def test_tool_update_completes_task():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ echo hi", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1",
                                              status="completed", body="hi")))
    a = _active(fs)
    assert a.tool.status == ToolStatus.DONE
    assert a.tasks[0].status == "done"


def test_tokens_update():
    fs = reduce(initial_snapshot(), TokensUpdated(1234))
    assert _active(fs).tokens == 1234


def test_permission_open_close():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ echo", status="pending")))
    fs = reduce(fs, PermissionOpened())
    assert _active(fs).state == AgentState.AWAITING_PERMISSION
    fs = reduce(fs, PermissionClosed())
    assert _active(fs).state == AgentState.RUNNING_TOOL


def test_turn_ended_ok_and_fail():
    ok = reduce(reduce(initial_snapshot(), TurnStarted()), TurnEnded(ok=True))
    assert _active(ok).state == AgentState.DONE
    bad = reduce(reduce(initial_snapshot(), TurnStarted()), TurnEnded(ok=False))
    assert _active(bad).state == AgentState.FAILED


def test_reduce_is_pure_returns_new_object():
    fs0 = initial_snapshot()
    fs1 = reduce(fs0, TurnStarted())
    assert fs0.active.state == AgentState.IDLE   # original unchanged
    assert fs1 is not fs0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_state.py -q`
Expected: FAIL with `ImportError: cannot import name 'reduce'`

- [ ] **Step 3: Write minimal implementation**

Append to `harness/tui/state.py`:

```python
# ---- reducer events ----

@dataclass(frozen=True)
class TurnStarted: ...

@dataclass(frozen=True)
class TurnEnded:
    ok: bool = True

@dataclass(frozen=True)
class ItemReceived:
    item: object              # a render.RenderedItem (duck-typed: .kind/.title/.status/.id)

@dataclass(frozen=True)
class TokensUpdated:
    total: int

@dataclass(frozen=True)
class PermissionOpened: ...

@dataclass(frozen=True)
class PermissionClosed: ...


def _tool_status(raw: str) -> ToolStatus:
    s = str(raw)
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return {
        "pending": ToolStatus.PENDING,
        "in_progress": ToolStatus.ACTIVE,
        "active": ToolStatus.ACTIVE,
        "completed": ToolStatus.DONE,
        "failed": ToolStatus.FAILED,
    }.get(s, ToolStatus.ACTIVE)


def _task_status_from_tool(ts: ToolStatus) -> str:
    return {ToolStatus.DONE: "done", ToolStatus.FAILED: "failed"}.get(ts, "in_progress")


def _reduce_agent(a: AgentSnapshot, event) -> AgentSnapshot:
    if isinstance(event, TurnStarted):
        return replace(a, state=AgentState.THINKING, activity_label="Thinking…",
                       tool=None, decision=None, tasks=(), elapsed=0.0)
    if isinstance(event, TokensUpdated):
        return replace(a, tokens=event.total)
    if isinstance(event, PermissionOpened):
        return replace(a, state=AgentState.AWAITING_PERMISSION)
    if isinstance(event, PermissionClosed):
        nxt = AgentState.RUNNING_TOOL if a.tool is not None else AgentState.RESPONDING
        return replace(a, state=nxt)
    if isinstance(event, TurnEnded):
        return replace(a, state=AgentState.DONE if event.ok else AgentState.FAILED,
                       tool=None, activity_label="")
    if isinstance(event, ItemReceived):
        item = event.item
        kind = getattr(item, "kind", "")
        if kind == "message":
            return replace(a, state=AgentState.RESPONDING, activity_label="Responding…")
        if kind == "tool":
            ts = _tool_status(getattr(item, "status", ""))
            title = getattr(item, "title", "")
            subtype = infer_subtype(title)
            tool = ToolView(title=title, status=ts, subtype=subtype)
            tasks = a.tasks + (TaskItem(label=title, status="in_progress"),)
            return replace(a, state=AgentState.RUNNING_TOOL, tool=tool, tasks=tasks,
                           activity_label=f"Running {subtype}")
        if kind == "tool_update":
            ts = _tool_status(getattr(item, "status", ""))
            tool = replace(a.tool, status=ts) if a.tool is not None else None
            new_task_status = _task_status_from_tool(ts)
            tasks = tuple(
                replace(t, status=new_task_status) if i == len(a.tasks) - 1 else t
                for i, t in enumerate(a.tasks)
            ) if a.tasks else a.tasks
            return replace(a, tool=tool, tasks=tasks)
    return a


def reduce(snapshot: FleetSnapshot, event) -> FleetSnapshot:
    """Pure: fold one event into the snapshot, updating the ACTIVE agent only
    (single-agent today; fleet fan-out later targets event.agent_id)."""
    agents = tuple(
        _reduce_agent(a, event) if a.id == snapshot.active_id else a
        for a in snapshot.agents
    )
    return FleetSnapshot(agents=agents, active_id=snapshot.active_id)
```

Note: `replace`, `field` must be imported from `dataclasses` (already in the Task-3 import line: `from dataclasses import dataclass, field, replace`). If `field` is unused, drop it to keep the import clean.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_state.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): pure reduce() over agent-state events

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Decision recognition from the harness meta chip

**Files:**
- Modify: `harness/tui/state.py`
- Test: `tests/test_tui_state.py` (extend)

**Interfaces:**
- Produces:
  - `def decision_from_meta(field_meta: dict | None) -> DecisionView | None` — reads `field_meta["harness"]["decision"]` of shape `{"question": str, "options": [{"title":..,"rationale":..}, ...]}`; returns `None` if absent/malformed (never raises).
  - New event `@dataclass(frozen=True) DecisionOpened(view: DecisionView)`; reducer sets state `AWAITING_DECISION` and `decision=view`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_state.py
from harness.tui.state import decision_from_meta, DecisionOpened


def test_decision_from_meta_parses():
    fm = {"harness": {"decision": {
        "question": "Where should the seam live?",
        "options": [
            {"title": "Wrapper", "rationale": "isolated"},
            {"title": "Patch upstream", "rationale": "violates zero-edits"},
        ]}}}
    dv = decision_from_meta(fm)
    assert dv is not None
    assert dv.question.startswith("Where")
    assert dv.options[0] == ("Wrapper", "isolated")


def test_decision_from_meta_malformed_returns_none():
    assert decision_from_meta(None) is None
    assert decision_from_meta({}) is None
    assert decision_from_meta({"harness": {"decision": {}}}) is None
    assert decision_from_meta({"harness": "x"}) is None


def test_decision_opened_sets_state():
    from harness.tui.state import DecisionView
    dv = DecisionView(question="q?", options=(("a", "b"),))
    fs = reduce(initial_snapshot(), DecisionOpened(dv))
    a = fs.active
    assert a.state == AgentState.AWAITING_DECISION
    assert a.decision == dv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_state.py::test_decision_from_meta_parses -q`
Expected: FAIL with `ImportError: cannot import name 'decision_from_meta'`

- [ ] **Step 3: Write minimal implementation**

Append the event near the other events and add the handler branch in `_reduce_agent` (before the final `return a`):

```python
@dataclass(frozen=True)
class DecisionOpened:
    view: "DecisionView"
```

```python
    if isinstance(event, DecisionOpened):
        return replace(a, state=AgentState.AWAITING_DECISION, decision=event.view)
```

Add the parser at the end of the module:

```python
def decision_from_meta(field_meta: dict | None) -> DecisionView | None:
    """Recognize a clarification ('grill-me') request from the harness meta chip.
    Tolerant: any missing/malformed shape yields None, never raises. Swaps to a
    formal ACP signal later with no widget change (spec §5.1)."""
    if not isinstance(field_meta, dict):
        return None
    harness = field_meta.get("harness")
    if not isinstance(harness, dict):
        return None
    dec = harness.get("decision")
    if not isinstance(dec, dict):
        return None
    question = dec.get("question")
    raw_opts = dec.get("options")
    if not question or not isinstance(raw_opts, list) or not raw_opts:
        return None
    options = tuple(
        (str(o.get("title", "")), str(o.get("rationale", "")))
        for o in raw_opts if isinstance(o, dict) and o.get("title")
    )
    if not options:
        return None
    return DecisionView(question=str(question), options=options)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_state.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): recognize clarification decisions from the harness meta chip

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PHASE 3 — Headline widgets & app integration

### Task 8: `FleetUpdated` message

**Files:**
- Modify: `harness/tui/messages.py`
- Test: `tests/test_tui_state.py` (extend) or a new `tests/test_tui_messages.py`

**Interfaces:**
- Produces: `class FleetUpdated(Message)` carrying `snapshot: FleetSnapshot`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_messages.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.messages import FleetUpdated
from harness.tui.state import initial_snapshot


def test_fleet_updated_carries_snapshot():
    fs = initial_snapshot()
    msg = FleetUpdated(fs)
    assert msg.snapshot is fs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_messages.py -q`
Expected: FAIL with `ImportError: cannot import name 'FleetUpdated'`

- [ ] **Step 3: Write minimal implementation**

Append to `harness/tui/messages.py`:

```python
class FleetUpdated(Message):
    """The presentation model changed; widgets re-render from the new snapshot.
    Posted by the app after folding a session update through state.reduce()."""
    def __init__(self, snapshot: Any) -> None:
        super().__init__()
        self.snapshot = snapshot
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_messages.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/tui/messages.py tests/test_tui_messages.py
git commit -m "feat(tui): FleetUpdated message (snapshot handoff)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `StatusChip`, `StateDot`, `ActivityGlyph`

**Files:**
- Create: `harness/tui/widgets/status_chip.py`
- Test: `tests/test_tui_widgets.py`

**Interfaces:**
- Consumes: `AgentState`, `ToolStatus` (state.py); `GLYPH`, `STATUS_LABEL` (tokens.py).
- Produces:
  - `class StatusChip(Static)` — `__init__(self, label: str, color_token: str)`; renders `[b]{LABEL}[/b]` colored via the token. Class method `from_state(state) -> StatusChip`.
  - `class StateDot(Static)` — `__init__(self, state)`; renders the glyph in the state's color token.
  - `class ActivityGlyph(Static)` — the single looping animation; cycles `◐◓◑◒` on a `set_interval(0.15)`; `reduced_motion: bool = False` renders a static `◐`.
  - Module helper `state_color_token(state) -> str` → token name string (`"accent"`, `"success"`, `"scheduled"`, `"error"`, `"muted"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_widgets.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from textual.app import App, ComposeResult

from harness.tui.state import AgentState
from harness.tui.widgets.status_chip import (
    StatusChip, StateDot, ActivityGlyph, state_color_token,
)


def test_state_color_token_mapping():
    assert state_color_token(AgentState.RUNNING_TOOL) == "accent"
    assert state_color_token(AgentState.DONE) == "success"
    assert state_color_token(AgentState.SCHEDULED) == "scheduled"
    assert state_color_token(AgentState.FAILED) == "error"
    assert state_color_token(AgentState.IDLE) == "muted"


def test_status_chip_renders_uppercase_label():
    chip = StatusChip.from_state(AgentState.RUNNING_TOOL)
    # the rendered markup contains the uppercase chip label
    assert "RUNNING" in str(chip.renderable) or "RUNNING" in chip._label


def test_activity_glyph_reduced_motion_is_static():
    g = ActivityGlyph(reduced_motion=True)
    assert g._frames_static is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_widgets.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.tui.widgets.status_chip'`

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tui/widgets/status_chip.py
"""Atomic status widgets for the design system: StatusChip (uppercase pill),
StateDot (leading glyph), ActivityGlyph (the ONE looping animation). All read the
shared token vocabulary; colors come from the theme. See spec §6 / components.md."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import AgentState
from harness.tui.tokens import GLYPH, STATUS_LABEL

_STATE_TOKEN = {
    AgentState.IDLE: "muted",
    AgentState.THINKING: "accent",
    AgentState.RESPONDING: "accent",
    AgentState.RUNNING_TOOL: "accent",
    AgentState.AWAITING_PERMISSION: "scheduled",
    AgentState.AWAITING_DECISION: "scheduled",
    AgentState.SCHEDULED: "scheduled",
    AgentState.DONE: "success",
    AgentState.FAILED: "error",
}

_STATE_GLYPH = {
    AgentState.IDLE: "idle",
    AgentState.THINKING: "active",
    AgentState.RESPONDING: "responding",
    AgentState.RUNNING_TOOL: "active",
    AgentState.AWAITING_PERMISSION: "awaiting",
    AgentState.AWAITING_DECISION: "awaiting",
    AgentState.SCHEDULED: "scheduled",
    AgentState.DONE: "done",
    AgentState.FAILED: "failed",
}


def state_color_token(state: AgentState) -> str:
    return _STATE_TOKEN.get(state, "muted")


class StatusChip(Static):
    def __init__(self, label: str, color_token: str) -> None:
        super().__init__(markup=True)
        self._label = label
        self._token = color_token
        self.update(f"[${color_token}][b]{label}[/b][/]")

    @classmethod
    def from_state(cls, state: AgentState) -> "StatusChip":
        label = STATUS_LABEL.get(state.value, state.value.upper())
        return cls(label, state_color_token(state))


class StateDot(Static):
    def __init__(self, state: AgentState) -> None:
        super().__init__(markup=True)
        glyph = GLYPH[_STATE_GLYPH.get(state, "idle")]
        self.update(f"[${state_color_token(state)}]{glyph}[/]")


class ActivityGlyph(Static):
    """The single looping animation in the UI: a quiet spinner of half-moons.
    reduced_motion → a static ◐ (no timer)."""
    _CYCLE = ["◐", "◓", "◑", "◒"]

    def __init__(self, reduced_motion: bool = False) -> None:
        super().__init__(markup=True)
        self._frames_static = reduced_motion
        self._i = 0

    def on_mount(self) -> None:
        self.update("[$accent]◐[/]")
        if not self._frames_static:
            self.set_interval(0.15, self._tick)

    def _tick(self) -> None:
        self._i = (self._i + 1) % len(self._CYCLE)
        self.update(f"[$accent]{self._CYCLE[self._i]}[/]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_widgets.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/status_chip.py tests/test_tui_widgets.py
git commit -m "feat(tui): StatusChip / StateDot / ActivityGlyph primitives

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `ActivityStatus` widget

**Files:**
- Create: `harness/tui/widgets/activity_status.py`
- Test: `tests/test_tui_widgets.py` (extend)

**Interfaces:**
- Consumes: `AgentSnapshot`.
- Produces: `class ActivityStatus(Static)` — `update_from(snapshot: AgentSnapshot)` renders `◐ <activity_label>… (Xs · ↓ N tokens)`. Uses `ActivityGlyph` semantics inline (one looping glyph via `set_interval`); when state is terminal (DONE/FAILED/IDLE) it stops animating and shows nothing or a settled line. Elapsed is provided by the snapshot (the app ticks it); the widget does not own the clock.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_widgets.py
from harness.tui.state import AgentSnapshot, AgentState
from harness.tui.widgets.activity_status import ActivityStatus


def test_activity_status_renders_label_elapsed_tokens():
    w = ActivityStatus()
    snap = AgentSnapshot(id="default", name="agent", state=AgentState.RESPONDING,
                         activity_label="Responding", elapsed=78.0, tokens=4000)
    text = w.line_for(snap)
    assert "Responding" in text
    assert "4.0" in text or "4000" in text     # token formatting
    assert "78" in text or "1m" in text         # elapsed formatting


def test_activity_status_blank_when_idle():
    w = ActivityStatus()
    snap = AgentSnapshot(id="default", name="agent", state=AgentState.IDLE)
    assert w.line_for(snap).strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_widgets.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tui/widgets/activity_status.py
"""ActivityStatus — the live work line: '◐ <label>… (1m 18s · ↓ 4.0k tokens)'.
Supersedes the bare LoadingIndicator. Reads an AgentSnapshot; the app supplies
elapsed (it owns the clock). Animates one glyph while working; blank when idle/
terminal. See spec §6 / components.md C."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import AgentSnapshot, AgentState

_WORKING = {AgentState.THINKING, AgentState.RESPONDING, AgentState.RUNNING_TOOL,
            AgentState.AWAITING_PERMISSION, AgentState.AWAITING_DECISION}
_CYCLE = ["◐", "◓", "◑", "◒"]


def _fmt_elapsed(s: float) -> str:
    s = int(s)
    return f"{s//60}m {s%60:02d}s" if s >= 60 else f"{s}s"


def _fmt_tokens(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


class ActivityStatus(Static):
    def __init__(self) -> None:
        super().__init__(markup=True)
        self._i = 0
        self._snap: AgentSnapshot | None = None

    def on_mount(self) -> None:
        self.set_interval(0.15, self._tick)

    def line_for(self, snap: AgentSnapshot, glyph: str = "◐") -> str:
        if snap.state not in _WORKING:
            return ""
        label = snap.activity_label or "Working"
        meta = f"{_fmt_elapsed(snap.elapsed)} · ↓ {_fmt_tokens(snap.tokens)} tokens"
        return f"[$accent]{glyph}[/] [$foreground]{label}…[/] [$muted]({meta})[/]"

    def update_from(self, snap: AgentSnapshot) -> None:
        self._snap = snap
        self._render()

    def _tick(self) -> None:
        if self._snap is None or self._snap.state not in _WORKING:
            return
        self._i = (self._i + 1) % len(_CYCLE)
        self._render()

    def _render(self) -> None:
        if self._snap is None:
            self.update("")
            return
        self.update(self.line_for(self._snap, _CYCLE[self._i]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_widgets.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/activity_status.py tests/test_tui_widgets.py
git commit -m "feat(tui): ActivityStatus live work line (label · elapsed · tokens)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: `TaskTree` and `ToolCallRow`

**Files:**
- Create: `harness/tui/widgets/task_tree.py`, `harness/tui/widgets/tool_call_row.py`
- Test: `tests/test_tui_widgets.py` (extend)

**Interfaces:**
- Consumes: `TaskItem`, `ToolView`, `ToolStatus`; `GLYPH`.
- Produces:
  - `class TaskTree(Static)` — `update_tasks(tasks: tuple[TaskItem, ...])`; renders one line per task with glyph `✓` done / `▣` in_progress / `□` pending / `✗` failed. `lines_for(tasks) -> list[str]` (pure, testable).
  - `class ToolCallRow(Static)` — `__init__(self, tool: ToolView)`; renders `<subtype-glyph> <title>` + a `StatusChip`. `line_for(tool) -> str` (pure, testable).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_widgets.py
from harness.tui.state import TaskItem, ToolView, ToolStatus
from harness.tui.widgets.task_tree import TaskTree
from harness.tui.widgets.tool_call_row import ToolCallRow


def test_task_tree_glyphs():
    tt = TaskTree()
    lines = tt.lines_for((
        TaskItem("explore", "done"),
        TaskItem("ask", "in_progress"),
        TaskItem("plan", "pending"),
        TaskItem("boom", "failed"),
    ))
    assert "✓" in lines[0] and "explore" in lines[0]
    assert "▣" in lines[1]
    assert "□" in lines[2]
    assert "✗" in lines[3]


def test_tool_call_row_line():
    row = ToolCallRow(ToolView(title="$ pytest tests/", status=ToolStatus.ACTIVE, subtype="test"))
    line = row.line_for(row._tool)
    assert "⚑" in line                # test subtype glyph
    assert "pytest" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_widgets.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tui/widgets/task_tree.py
"""TaskTree — the live checklist (✓ done / ▣ in-progress / □ pending / ✗ failed),
updated in place. Reads a tuple of TaskItem. See spec §6 / components.md C."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import TaskItem

_GLYPH = {"done": ("✓", "success"), "in_progress": ("▣", "accent"),
          "pending": ("□", "muted"), "failed": ("✗", "error")}


class TaskTree(Static):
    def __init__(self) -> None:
        super().__init__(markup=True)

    def lines_for(self, tasks: tuple[TaskItem, ...]) -> list[str]:
        out = []
        for t in tasks:
            glyph, token = _GLYPH.get(t.status, ("□", "muted"))
            label = t.label[2:] if t.label.startswith("$ ") else t.label
            out.append(f"[${token}]{glyph}[/] [$foreground]{label}[/]")
        return out

    def update_tasks(self, tasks: tuple[TaskItem, ...]) -> None:
        self.update("\n".join(self.lines_for(tasks)))
```

```python
# harness/tui/widgets/tool_call_row.py
"""ToolCallRow — one tool call: subtype glyph + title + status chip. Reads a
ToolView. Subtype glyph is inferred (display-only). See spec §6 / components.md C."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import ToolView, ToolStatus
from harness.tui.tokens import GLYPH

_STATUS_TOKEN = {ToolStatus.PENDING: "scheduled", ToolStatus.ACTIVE: "accent",
                 ToolStatus.DONE: "success", ToolStatus.FAILED: "error"}
_STATUS_LABEL = {ToolStatus.PENDING: "QUEUED", ToolStatus.ACTIVE: "RUNNING",
                 ToolStatus.DONE: "COMPLETED", ToolStatus.FAILED: "FAILED"}


class ToolCallRow(Static):
    def __init__(self, tool: ToolView) -> None:
        super().__init__(markup=True)
        self._tool = tool
        self.update(self.line_for(tool))

    def line_for(self, tool: ToolView) -> str:
        glyph = GLYPH.get(tool.subtype, GLYPH["shell"])
        title = tool.title[2:] if tool.title.startswith("$ ") else tool.title
        token = _STATUS_TOKEN.get(tool.status, "muted")
        label = _STATUS_LABEL.get(tool.status, "")
        return (f"[${token}]{glyph}[/] [$foreground]{title}[/]   "
                f"[${token}][b]{label}[/b][/]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_widgets.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/task_tree.py harness/tui/widgets/tool_call_row.py tests/test_tui_widgets.py
git commit -m "feat(tui): TaskTree + ToolCallRow widgets

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: `DecisionPrompt` (inline) widget

**Files:**
- Create: `harness/tui/widgets/decision_prompt.py`
- Test: `tests/test_tui_widgets.py` (extend)

**Interfaces:**
- Consumes: `DecisionView`.
- Produces:
  - `class DecisionPrompt(Vertical)` — `__init__(self, view: DecisionView)`; renders the question, numbered options (title accented + dimmed rationale), and two fallback rows (`Type something`, `Chat about this`). `↑/↓` move, `enter`/number selects; posts a `DecisionPrompt.Selected(index)` message (index `-1` = Type something, `-2` = Chat). Pure helper `option_lines() -> list[str]` for testing.
  - Nested `class Selected(Message)` with `.index: int`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tui_widgets.py
from harness.tui.state import DecisionView
from harness.tui.widgets.decision_prompt import DecisionPrompt


def test_decision_prompt_option_lines():
    dv = DecisionView(question="Where should the seam live?",
                      options=(("Wrapper", "isolated, recommended"),
                               ("Patch upstream", "violates zero-edits")))
    dp = DecisionPrompt(dv)
    lines = dp.option_lines()
    # numbered options + 2 fallbacks
    assert any("1." in ln and "Wrapper" in ln for ln in lines)
    assert any("isolated" in ln for ln in lines)
    assert any("Type something" in ln for ln in lines)
    assert any("Chat about this" in ln for ln in lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_widgets.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tui/widgets/decision_prompt.py
"""DecisionPrompt — the inline 'grill-me' clarification UI: a question + numbered
options (title + dimmed rationale) + 'Type something' / 'Chat about this'
fallbacks. Display + selection only; the app acts on the Selected message. The
same model escalates to a modal when blocking (app's choice of mount target).
See spec §6 / components.md D."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from harness.tui.state import DecisionView

TYPE_SOMETHING = -1
CHAT_ABOUT_IT = -2


class DecisionPrompt(Vertical):
    class Selected(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, view: DecisionView) -> None:
        super().__init__(id="decision-prompt")
        self._view = view
        self._cursor = 0          # 0..n-1 options, then fallbacks
        self._n = len(view.options)

    def option_lines(self) -> list[str]:
        lines: list[str] = []
        for i, (title, rationale) in enumerate(self._view.options, start=1):
            lines.append(f"[$accent]{i}. {title}[/]")
            if rationale:
                lines.append(f"     [$muted]{rationale}[/]")
        lines.append(f"[$muted]{self._n + 1}. Type something[/]")
        lines.append(f"[$muted]{self._n + 2}. Chat about this[/]")
        return lines

    def compose(self) -> ComposeResult:
        yield Static(f"[$foreground]{self._view.question}[/]", markup=True,
                     id="decision-question")
        yield Static("\n".join(self.option_lines()), markup=True, id="decision-options")

    def move(self, delta: int) -> None:
        total = self._n + 2          # options + 2 fallbacks
        self._cursor = max(0, min(total - 1, self._cursor + delta))

    def select(self) -> None:
        if self._cursor < self._n:
            self.post_message(self.Selected(self._cursor))
        elif self._cursor == self._n:
            self.post_message(self.Selected(TYPE_SOMETHING))
        else:
            self.post_message(self.Selected(CHAT_ABOUT_IT))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tui_widgets.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/decision_prompt.py tests/test_tui_widgets.py
git commit -m "feat(tui): DecisionPrompt inline clarification widget

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Wire the reducer + ActivityStatus/TaskTree into `app.py`

**Files:**
- Modify: `harness/tui/app.py`, `harness/tui/app.tcss`
- Test: `tests/test_tui_pilot.py` (extend)

**Interfaces:**
- Consumes: `reduce`, `initial_snapshot`, the event constructors, `decision_from_meta` (state.py); `FleetUpdated` (messages.py); `ActivityStatus`, `TaskTree` (widgets).
- Produces: the app holds `self._snapshot: FleetSnapshot`; `on_session_update` folds each rendered item / chip / token update through `reduce()` and refreshes an `ActivityStatus` + `TaskTree` mounted in the transcript region; `_send_prompt` dispatches `TurnStarted`/`TurnEnded`; `_show_working` delegates to `ActivityStatus`.

**This task keeps:** the `gen`/`session_id` guards, the streaming-Markdown `_stream_message` path, the chip lines, and all permission/cancel/clear plumbing. It adds the snapshot fold alongside them; it does not remove the existing transcript rendering of messages/thoughts/user lines.

- [ ] **Step 1: Write the failing pilot test**

```python
# append to tests/test_tui_pilot.py
def test_pilot_snapshot_tracks_turn_lifecycle():
    """After sending a prompt, the app's snapshot leaves IDLE; after the turn
    completes it reaches a terminal state. Proves on_session_update routes
    through the reducer."""
    from harness.tui.state import AgentState
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._snapshot.active.state == AgentState.IDLE
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            assert app._snapshot.active.state in (AgentState.DONE, AgentState.RESPONDING), \
                f"snapshot did not advance: {app._snapshot.active.state}"
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tui_pilot.py::test_pilot_snapshot_tracks_turn_lifecycle -q`
Expected: FAIL with `AttributeError: 'HarnessTui' object has no attribute '_snapshot'`

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/app.py`:

1. Add imports near the other `harness.tui` imports:
```python
from harness.tui.state import (
    initial_snapshot, reduce, TurnStarted, TurnEnded, ItemReceived,
    TokensUpdated, DecisionOpened, decision_from_meta,
)
from harness.tui.messages import SessionUpdate, PermissionRequest, FleetUpdated
from harness.tui.widgets.activity_status import ActivityStatus
from harness.tui.widgets.task_tree import TaskTree
```
   (Merge the `messages` import with the existing one rather than duplicating.)

2. In `__init__`, after `self._tokens = 0`, add:
```python
        self._snapshot = initial_snapshot()       # the presentation model
```

3. Add a helper to fold an event and refresh the activity widgets:
```python
    def _apply(self, event) -> None:
        self._snapshot = reduce(self._snapshot, event)
        active = self._snapshot.active
        if active is None or not self._started:
            return
        try:
            self.query_one("#activity", ActivityStatus).update_from(active)
        except Exception:
            pass
        try:
            self.query_one("#tasktree", TaskTree).update_tasks(active.tasks)
        except Exception:
            pass
```

4. In `_enter_conversation`, after mounting the composer, mount the activity region into the transcript-adjacent area. Add, before `self._refresh_status()`:
```python
        await self.mount(ActivityStatus(id="activity"), before="#statusbar")
        await self.mount(TaskTree(id="tasktree"), before="#statusbar")
```

5. In `on_input_submitted`, after `self._turn_start = time.monotonic()` add:
```python
        self._apply(TurnStarted())
```
   And add a `set_interval` elapsed tick once (in `on_mount`, after `_connect` or near it):
```python
        self.set_interval(0.25, self._tick_elapsed)
```
   with:
```python
    def _tick_elapsed(self) -> None:
        if self._snapshot.active and self._snapshot.active.state.value in (
                "thinking", "responding", "running_tool",
                "awaiting_permission", "awaiting_decision"):
            from dataclasses import replace
            a = self._snapshot.active
            elapsed = time.monotonic() - self._turn_start
            agents = tuple(replace(x, elapsed=elapsed) if x.id == a.id else x
                           for x in self._snapshot.agents)
            self._snapshot = type(self._snapshot)(agents=agents,
                                                  active_id=self._snapshot.active_id)
            try:
                self.query_one("#activity", ActivityStatus).update_from(self._snapshot.active)
            except Exception:
                pass
```

6. In `on_session_update`, after the existing `for chip in harness_chips(...)` block and the `item = render_update(...)` call, fold into the model. Insert right after `self._maybe_update_tokens(...)`:
```python
        # fold into the presentation model (alongside the existing rendering)
        dv = decision_from_meta(getattr(msg.update, "field_meta", None))
        if dv is not None:
            self._apply(DecisionOpened(dv))
```
   and after `item = render_update(msg.update)` (when `item is not None`), add:
```python
        if item is not None:
            self._apply(ItemReceived(item))
```
   (Place this so it runs for every item; the existing `if item.kind == ...` rendering below is unchanged.)
   In `_maybe_update_tokens`, after `self._tokens = usage["total"]`, add:
```python
            self._apply(TokensUpdated(self._tokens))
```

7. In `_send_prompt`, in the `try` after a successful `prompt(...)` returns, add `self._apply(TurnEnded(ok=True))`; in the `except` add `self._apply(TurnEnded(ok=False))`.

8. `_show_working` / `_hide_working`: keep them (they still mount the LoadingIndicator as a fallback), OR replace the body of `_show_working` to no-op now that `ActivityStatus` shows progress. Minimal change: leave them; `ActivityStatus` is additive.

In `harness/tui/app.tcss`, add:
```css
/* design-system activity region */
#activity { height: 1; padding: 0 2; color: $accent; }
#tasktree { height: auto; padding: 0 2; color: $foreground; }
#decision-prompt { padding: 1 2; border-left: thick $accent; background: $surface; }
#decision-question { text-style: bold; padding-bottom: 1; }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_tui_pilot.py::test_pilot_snapshot_tracks_turn_lifecycle -q`
Expected: PASS

- [ ] **Step 5: Run the full TUI suite (no regressions)**

Run: `python -m pytest tests/ -q`
Expected: PASS (all). If a pre-existing test asserts on `#working`, confirm `_show_working` still mounts it; adjust only if this task changed that behavior.

- [ ] **Step 6: Commit**

```bash
git add harness/tui/app.py harness/tui/app.tcss tests/test_tui_pilot.py
git commit -m "feat(tui): route session updates through the reducer; show ActivityStatus + TaskTree

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after Task 13)

- [ ] Run the whole suite from the worktree root: `python -m pytest tests/ -q` → all green.
- [ ] Manual smoke (optional): `./run.sh --model mock` and confirm the activity line shows label · elapsed · tokens during a turn and a task appears in the tree as the mock tool runs; reset with `git checkout examples/sample-repo/calculator.py`.
- [ ] Confirm no hardcoded hex landed outside `theme.py` (grep): `grep -rnE '#[0-9A-Fa-f]{6}' harness/tui/widgets harness/tui/state.py harness/tui/tokens.py` → no matches.

---

## Self-Review notes (coverage map)

- Spec §4 tokens → Tasks 1–2. Spec §5 state model + reducer → Tasks 3–7. Spec §6 widgets → Tasks 9–12. Spec §7 app integration → Tasks 8, 13.
- `ProgressRow`, `ScheduleBadge`/`CronRow`, and the fleet shell (`AppShell`/`AgentRail`/`SidebarToggle`/`FleetHeader`) are spec Phase 4 (fleet-gated) — intentionally **out of scope** for this Phases 1–3 plan; they get their own plan when fleet engine data exists.
- `AnswerStream`/`UserMessage`/`PermissionModal`/`SelectModal`/`StatusBar` already exist and are kept unchanged; no task rewrites them.
