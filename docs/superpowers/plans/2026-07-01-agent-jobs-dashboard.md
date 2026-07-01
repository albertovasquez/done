# Agent Jobs Dashboard (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-agent, jobs-first activity dashboard — open with `J` on a focused agent to see that agent's jobs in a rich table (TASK · STATUS · PROGRESS · ELAPSED) with a command input, built snapshot-driven.

**Architecture:** A pure view model (`harness/jobs/view.py`) maps real `Job`/`JobState` → `JobRow`; a dumb `JobsTable` widget renders rows with design-system tokens; an `AgentDashboard` `ModalScreen` composes the table + header + command input; `app.py` opens it with `J`. Data flows one way: `jobs.store` → `view.job_rows()` → `JobsTable`.

**Tech Stack:** Python 3.11, Textual 8.2.7, pytest (<9), pytest-textual-snapshot, existing `harness.jobs` backend, existing snapshot harness (`tests/tui_snapshot_harness.py`).

## Global Constraints

- **Worktree only:** `.worktrees/agent-jobs-dashboard` on `feat/agent-jobs-dashboard`. Never edit `main`/primary (AGENTS.md #1).
- **Shared venv:** `../../.venv/bin/python`. Test cmd: `../../.venv/bin/python -m pytest tests/ -q`.
- **Progress is `None` in Phase 1** — no fraction source exists in the backend. `JobRow.progress` is always `None`; the PROGRESS column renders `—`. No fabricated bars (#252 rule).
- **Header shows name · state only** — no uptime/load (no truthful source).
- **Truthful status derivation:** `RUNNING` (running_since set), `SCHEDULED` (enabled + next_run_at), `DISABLED` (not enabled), `COMPLETED`/`FAILED` (from last_status when not armed). `QUEUED` modeled but rare — no dedicated UI/snapshot.
- **Command-first input** — job verbs via `ops` (run/update-disable/remove); NO agent session (P1). Free chat deferred.
- **Design system:** status pills via `StatusChip(label, color_token)` + `state_color_token`; glyphs from `harness.tui.tokens.GLYPH`; colors from `harness.tui.theme` (`STATUS_COLOR`). No hardcoded hex. New components → catalog entry in `components.md` (AGENTS.md §7).
- **Baseline-acceptance rule:** judge each snapshot SVG vs `components.md` + `docs/superpowers/specs/2026-06-26-tui-design-system-design.md` before committing it. Baselines live under `tests/__snapshots__/<module>/<test>.svg` (committed); `snapshot_report.html` is git-ignored.
- **Real APIs (verified):**
  - `harness.jobs.ops.list_jobs(include_disabled=True, agent_id=None) -> list[Job]`
  - `Job(id, name, agent_id, description, enabled, schedule, payload)`; `schedule` ∈ `{At(when_iso), Every(seconds), Cron(expr,…), Dynamic()}`
  - `JobState(next_run_at, running_since, last_run_at, last_status, last_error, last_duration, consecutive_errors, version)` at `job.state`
  - `StatusChip(label: str, color_token: str)` (Static subclass)
  - snapshot harness exports: `REPO`, `FAKE_CMD` from `tests/tui_snapshot_harness.py`

---

### Task 1: P1a — view model (`view.py`) + `JobsTable` widget, snapshot-driven

**Files:**
- Create: `harness/jobs/view.py`
- Create: `harness/tui/widgets/jobs_table.py`
- Create: `tests/test_jobs_view.py`
- Create: `tests/test_tui_jobs_table_snapshots.py`
- Modify: `harness/tui/styles/components.md` (catalog entry for `JobsTable`)

**Interfaces:**
- Consumes: `harness.jobs.ops.list_jobs`, `harness.jobs.model` (`Job`, `JobState`, schedule types), `harness.tui.widgets.status_chip`, `harness.tui.tokens.GLYPH`.
- Produces:
  - `harness.jobs.view.JobRow` — frozen dataclass: `name: str, description: str, status: str, progress: float | None, when: str, elapsed: str`.
  - `harness.jobs.view.derive_status(job, now) -> str` — one of `RUNNING|SCHEDULED|DISABLED|COMPLETED|FAILED|QUEUED`.
  - `harness.jobs.view.job_rows(agent_id: str, now: float) -> tuple[JobRow, ...]`.
  - `harness.tui.widgets.jobs_table.JobsTable(Static)` with `set_rows(rows: tuple[JobRow, ...]) -> None`.

- [ ] **Step 1: Write failing unit tests for status derivation + row mapping**

Create `tests/test_jobs_view.py`:

```python
from dataclasses import replace
from harness.jobs import model as m
from harness.jobs.view import JobRow, derive_status, job_rows


def _job(**kw):
    base = dict(id="j1", name="Nightly sync", agent_id="fred",
                description="Syncs data", enabled=True,
                schedule=m.Every(seconds=3600), payload=m.AgentTurn(message="go"))
    base.update(kw)
    st = base.pop("state", m.JobState())
    return m.Job(**base), st


def test_status_running():
    j, st = _job(state=m.JobState(running_since=100.0))
    assert derive_status(m.Job(**{**j.__dict__}), now=200.0) if False else \
        derive_status(j._replace(state=st) if hasattr(j, "_replace") else j, now=200.0) == "RUNNING"
```

NOTE to implementer (VERIFIED): `Job.state` IS an inline field
(`Job(..., state: JobState, ...)`). BUT `Job` has several other required fields
(`schedule, payload, grant, cost, state`) — do NOT hand-build `Job` in fixtures.
Instead seed via `harness.jobs.ops.add(...)` (which fills defaults) under an
isolated `XDG_CONFIG_HOME`, then set state through the store, OR build a tiny
fixture helper that supplies all required fields once. The status you want to
test is a function of `job.enabled` + `job.state` (running_since / next_run_at /
last_status) — set those. The ASSERTIONS below are the contract; wire fixtures to
the real `Job` shape:

```python
def test_derive_status_branches():
    now = 1000.0
    assert derive_status(_running_job(running_since=900.0), now) == "RUNNING"
    assert derive_status(_scheduled_job(next_run_at=now + 7200), now) == "SCHEDULED"
    assert derive_status(_disabled_job(), now) == "DISABLED"
    assert derive_status(_done_job(last_status="ok"), now) == "COMPLETED"
    assert derive_status(_done_job(last_status="error"), now) == "FAILED"


def test_progress_is_always_none_in_phase1():
    rows = job_rows("fred", now=1000.0)          # against a seeded store
    assert all(r.progress is None for r in rows)


def test_job_rows_scoped_to_agent():
    # only 'fred' jobs returned; a 'sam' job must not appear
    rows = job_rows("fred", now=1000.0)
    assert all("sam" not in r.name.lower() for r in rows)


def test_when_column_formats():
    assert format_when_scheduled(next_run_at=1000.0 + 2*86400 + 14*3600, now=1000.0) == "in 2d 14h"
    assert format_elapsed(running_since=1000.0 - 1122, now=1000.0) == "00:18:42"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/test_jobs_view.py -q`
Expected: FAIL (ImportError: no module `harness.jobs.view`).

- [ ] **Step 3: Implement `harness/jobs/view.py`**

```python
"""Pure view model for the agent jobs dashboard. No Textual. Maps the real
Job/JobState (harness.jobs) into flat JobRows the UI renders. Progress is always
None in Phase 1 — the backend exposes no fraction (verified)."""
from __future__ import annotations

from dataclasses import dataclass

from harness.jobs import model as m, ops


@dataclass(frozen=True)
class JobRow:
    name: str
    description: str
    status: str            # RUNNING|SCHEDULED|DISABLED|COMPLETED|FAILED|QUEUED
    progress: float | None # always None in P1 (no truthful source)
    when: str              # "in 2d 14h" | "" (running) | last-run relative
    elapsed: str           # "00:18:42" | "—"


def derive_status(job: m.Job, now: float) -> str:
    st = job.state
    if st.running_since is not None:
        return "RUNNING"
    if not job.enabled:
        return "DISABLED"
    if st.next_run_at is not None:
        if st.next_run_at <= now:
            return "QUEUED"           # due but not yet running (rare)
        return "SCHEDULED"
    if st.last_status == "error":
        return "FAILED"
    if st.last_status:
        return "COMPLETED"
    return "SCHEDULED"


def format_elapsed(running_since: float, now: float) -> str:
    s = max(0, int(now - running_since))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def format_when_scheduled(next_run_at: float, now: float) -> str:
    s = max(0, int(next_run_at - now))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600); mnt = rem // 60
    if d: return f"in {d}d {h}h"
    if h: return f"in {h}h {mnt}m"
    if mnt: return f"in {mnt}m"
    return "due"


def _row(job: m.Job, now: float) -> JobRow:
    status = derive_status(job, now)
    st = job.state
    if status == "RUNNING" and st.running_since is not None:
        when, elapsed = "", format_elapsed(st.running_since, now)
    elif status == "SCHEDULED" and st.next_run_at is not None:
        when, elapsed = format_when_scheduled(st.next_run_at, now), "—"
    elif status in ("COMPLETED", "FAILED") and st.last_duration is not None:
        when, elapsed = "", format_elapsed(now - st.last_duration, now)
    else:
        when, elapsed = "", "—"
    return JobRow(name=job.name, description=job.description, status=status,
                  progress=None, when=when, elapsed=elapsed)


def job_rows(agent_id: str, now: float) -> tuple[JobRow, ...]:
    try:
        jobs = ops.list_jobs(agent_id=agent_id)
    except Exception:
        return ()
    return tuple(_row(j, now) for j in jobs)
```

IMPLEMENTER: verify `job.state` is the correct accessor for `JobState` (read
`model.py`/`store.py`); if state lives in the store keyed by id, adjust `_row`
and `job_rows` to fetch it. Keep the public contract (JobRow fields, function
names) exactly as the Interfaces block declares.

- [ ] **Step 4: Run view tests green**

Run: `../../.venv/bin/python -m pytest tests/test_jobs_view.py -q`
Expected: PASS.

- [ ] **Step 5: Write failing snapshot tests for `JobsTable`**

Create `tests/test_tui_jobs_table_snapshots.py`. Drive a tiny host app that mounts
only `JobsTable` (like `tests/test_agent_rail.py` mounts `AgentRail`), fed FAKE
`JobRow`s — hermetic, no jobs backend:

```python
import pytest
from textual.app import App, ComposeResult
from harness.jobs.view import JobRow
from harness.tui.widgets.jobs_table import JobsTable
from harness.tui.theme import HARNESS_THEME

MIXED = (
    JobRow("Index repo dependencies", "Scanning package graphs", "RUNNING", None, "", "00:18:42"),
    JobRow("Nightly sync", "Syncing upstream", "RUNNING", None, "", "00:07:11"),
    JobRow("Refresh embeddings", "Rebuilding index", "QUEUED", None, "", "—"),
    JobRow("Weekly report cron", "Weekly reports", "SCHEDULED", None, "in 2d 14h", "—"),
    JobRow("Customer import", "Normalize data", "COMPLETED", None, "", "00:04:03"),
)


class _Host(App):
    def __init__(self, rows):
        super().__init__(); self._rows = rows
    def compose(self) -> ComposeResult:
        yield JobsTable(id="jt")
    def on_mount(self):
        self.register_theme(HARNESS_THEME); self.theme = "harness"
        self.query_one("#jt", JobsTable).set_rows(self._rows)


def test_jobs_table_mixed(snap_compare):
    assert snap_compare(_Host(MIXED), terminal_size=(120, 30))

def test_jobs_table_empty(snap_compare):
    assert snap_compare(_Host(()), terminal_size=(120, 30))

def test_jobs_table_scheduled_only(snap_compare):
    rows = tuple(r for r in MIXED if r.status == "SCHEDULED")
    assert snap_compare(_Host(rows), terminal_size=(120, 30))
```

IMPLEMENTER: confirm the exact theme-registration idiom from `tests/test_agent_rail.py`
(it already mounts a single widget with the harness theme) and mirror it.

- [ ] **Step 6: Run snapshot tests — expect missing-baseline failure**

Run: `../../.venv/bin/python -m pytest tests/test_tui_jobs_table_snapshots.py -q`
Expected: FAIL (snapshots don't exist yet).

- [ ] **Step 7: Implement `harness/tui/widgets/jobs_table.py`**

```python
"""JobsTable — dumb/reactive table for an agent's jobs. Given a tuple[JobRow],
renders TASK · STATUS · PROGRESS · ELAPSED using design-system tokens. No data
access. Progress is None in Phase 1 → renders '—' (no fabricated bars, #252)."""
from __future__ import annotations

from textual.widgets import Static

from harness.jobs.view import JobRow
from harness.tui.tokens import GLYPH

# status word -> theme color token (semantic; no hardcoded hex)
_STATUS_TOKEN = {
    "RUNNING": "accent", "SCHEDULED": "scheduled", "QUEUED": "muted",
    "COMPLETED": "success", "FAILED": "error", "DISABLED": "muted",
}


def _chip(status: str) -> str:
    tok = _STATUS_TOKEN.get(status, "muted")
    return f"[${tok}][b]{status}[/b][/]"


def _progress_cell(progress: float | None) -> str:
    if progress is None:
        return "[$muted]—[/]"
    filled = int(round(progress * 20))
    bar = "█" * filled + "░" * (20 - filled)
    return f"{int(progress*100)}% [$accent]{bar}[/]"


def render_table(rows: tuple[JobRow, ...]) -> str:
    if not rows:
        return "[$muted]No jobs for this agent — nothing scheduled.[/]"
    dot = GLYPH.get("running", "●")
    lines = ["[$muted]TASK                          STATUS        PROGRESS      ELAPSED[/]"]
    for r in rows:
        name = f"[$foreground][b]{r.name}[/b][/]"
        desc = f"[$muted]{r.description}[/]"
        cell = f"{name}  {_chip(r.status)}  {_progress_cell(r.progress)}  {r.elapsed}"
        lines.append(cell)
        lines.append(f"  {desc}")
    return "\n".join(lines)


class JobsTable(Static):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__("", *args, markup=True, **kwargs)
        self._rows: tuple[JobRow, ...] = ()

    def set_rows(self, rows: tuple[JobRow, ...]) -> None:
        self._rows = tuple(rows)
        self.update(render_table(self._rows))
```

IMPLEMENTER: `render_table` is deliberately a pure function returning markup, so
it can be unit-tested AND drives the widget — mirror the `agent_rail.card_markup`
pattern. Verify `_STATUS_TOKEN` keys resolve to real theme variables
(`HARNESS_THEME.variables` / `theme.py`); if `scheduled` is not a token, use the
nearest real one and note it. Align columns so the header and rows line up at
120 cols (adjust padding to match; the snapshot will show misalignment).

- [ ] **Step 8: Generate + JUDGE baselines, then run green**

Run: `../../.venv/bin/python -m pytest tests/test_tui_jobs_table_snapshots.py --snapshot-update -q`
Then OPEN each SVG under `tests/__snapshots__/test_tui_jobs_table_snapshots/` and
judge vs `components.md` + the design-system spec: columns aligned, status pills
use the right tokens, PROGRESS shows `—` (no bars), empty state clean. Iterate
`render_table` until the render matches the mockup's LAYOUT (honest columns) and
the style guide. Re-run without the flag: `... -q` → PASS.

- [ ] **Step 9: Add `JobsTable` catalog entry to `components.md`**

Add one entry under the appropriate section (follow the existing table/row entries'
format): name `JobsTable`, status `✅ shipped`, one-line "When to use" (an agent's
jobs list: TASK·STATUS·PROGRESS·ELAPSED; progress `—` until a real signal exists).

- [ ] **Step 10: Commit**

```bash
git add harness/jobs/view.py harness/tui/widgets/jobs_table.py \
        tests/test_jobs_view.py tests/test_tui_jobs_table_snapshots.py \
        tests/__snapshots__/test_tui_jobs_table_snapshots/ \
        harness/tui/styles/components.md
git commit -m "feat(jobs): view model + JobsTable widget (dashboard P1a)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: P1b — `AgentDashboard` screen + `J` navigation, wired to real jobs

**Files:**
- Create: `harness/tui/screens/agent_dashboard.py`
- Create: `harness/tui/screens/__init__.py` (if `screens/` doesn't exist)
- Modify: `harness/tui/app.py` (add `J` binding + open action)
- Create: `tests/test_agent_dashboard.py`
- Modify: `tests/test_tui_snapshots.py` (add a full-screen snapshot)

**Interfaces:**
- Consumes: `JobsTable`, `harness.jobs.view.job_rows`, `harness.jobs.ops.list_jobs`; the app's rail selection.
- Produces: `harness.tui.screens.agent_dashboard.AgentDashboard(ModalScreen)` — ctor `AgentDashboard(agent_id: str, agent_name: str)`; header shows `agent_name · state`; body is a `JobsTable`; `esc` closes. `HarnessTui.action_open_agent_dashboard()` bound to `J`.

- [ ] **Step 1: Write failing test — J opens the dashboard for the focused agent**

Create `tests/test_agent_dashboard.py`:

```python
import asyncio, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

from harness.tui.app import HarnessTui
from harness.tui.screens.agent_dashboard import AgentDashboard


def test_j_opens_agent_dashboard():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.action_open_agent_dashboard()   # direct action (rail focus not required)
            await pilot.pause()
            assert isinstance(app.screen, AgentDashboard), \
                f"J did not open the dashboard: {type(app.screen).__name__}"
    asyncio.run(go())
```

- [ ] **Step 2: Run — expect fail (no module / no action)**

Run: `../../.venv/bin/python -m pytest tests/test_agent_dashboard.py -q`
Expected: FAIL (ImportError / AttributeError).

- [ ] **Step 3: Implement the screen**

Create `harness/tui/screens/__init__.py` (empty) if needed, and
`harness/tui/screens/agent_dashboard.py`:

```python
"""AgentDashboard — a per-agent, jobs-first activity screen. Header (name·state)
+ JobsTable fed from the pure view model. esc closes. Progress is None in P1."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from harness.jobs.view import job_rows
from harness.tui.widgets.jobs_table import JobsTable


class AgentDashboard(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, agent_id: str, agent_name: str) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._agent_name = agent_name

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-dashboard"):
            yield Static(
                f"[$muted]ACTIVE AGENT[/]\n[$accent][b]{self._agent_name}[/b][/]",
                id="dashboard-header", markup=True)
            yield JobsTable(id="dashboard-jobs")

    def on_mount(self) -> None:
        rows = job_rows(self._agent_id, now=time.time())
        self.query_one("#dashboard-jobs", JobsTable).set_rows(rows)
```

- [ ] **Step 4: Wire `J` in `app.py`**

Add to `HarnessTui.BINDINGS` (near the existing `ctrl+j`): `("j", "open_agent_dashboard", "Agent dashboard")`. Add the action method (place near `action_toggle_cron`):

```python
    def action_open_agent_dashboard(self) -> None:
        """Open the per-agent jobs dashboard for the active persona."""
        snap = self._snapshot.active
        agent_id = snap.id if snap else "default"
        agent_name = snap.name if snap else "agent"
        self.push_screen(AgentDashboard(agent_id, agent_name))
```

Add the import at the top with the other screen/widget imports:
`from harness.tui.screens.agent_dashboard import AgentDashboard`.

IMPLEMENTER: verify `self._snapshot.active` exposes `.id`/`.name` (it does per
state.py `AgentSnapshot`). Confirm `"j"` doesn't collide with an existing
single-key binding by grepping `BINDINGS` in app.py; if the composer input eats
`j` while focused, that's fine — the binding fires when focus is on the
transcript/rail, matching "on a focused agent". Do NOT remove the `ctrl+j` cron
binding.

- [ ] **Step 5: Run behavior test green**

Run: `../../.venv/bin/python -m pytest tests/test_agent_dashboard.py -q`
Expected: PASS.

- [ ] **Step 5b: Test the actual `j` KEYPRESS (not just the action)** — caveman fix

The Step-1 test calls `action_open_agent_dashboard()` directly, so it would pass
even if the `j` binding were dead. Add a test that presses the key with focus OFF
any input, proving the binding is live:

```python
def test_j_keypress_opens_dashboard():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # move focus off the composer/input so a printable key isn't consumed
            app.set_focus(None)
            await pilot.press("j")
            await pilot.pause()
            assert isinstance(app.screen, AgentDashboard), \
                "pressing 'j' (focus off input) did not open the dashboard"
    asyncio.run(go())
```

Run: `../../.venv/bin/python -m pytest tests/test_agent_dashboard.py::test_j_keypress_opens_dashboard -q`
Expected: PASS. IMPLEMENTER: if `j` is consumed by a focused input in normal use,
that is CORRECT (typing 'j' should type, not open) — this test deliberately drops
focus first. If the binding truly can't fire, fall back to the `enter`-on-rail
path from the spec and update this test accordingly.

- [ ] **Step 6: Add a full-screen snapshot (real, empty jobs store → empty state)**

Add to `tests/test_tui_snapshots.py`:

```python
def test_agent_dashboard_screen(snap_compare):
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")

    async def run_before(pilot):
        await pilot.pause()
        await app.action_open_agent_dashboard()
        await pilot.pause()

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))
```

(Uses the existing `isolated_default_persona` autouse fixture in that module, so
the store is a fresh XDG dir → the dashboard shows the empty state. That's the
honest first full-screen baseline.)

- [ ] **Step 7: Generate + JUDGE the screen baseline, run green**

Run: `../../.venv/bin/python -m pytest tests/test_tui_snapshots.py::test_agent_dashboard_screen --snapshot-update -q`
Open the SVG; judge vs the style guide (header grammar, empty-jobs copy, modal
framing). Re-run without the flag → PASS.

- [ ] **Step 8: Commit**

```bash
git add harness/tui/screens/ harness/tui/app.py tests/test_agent_dashboard.py \
        tests/test_tui_snapshots.py tests/__snapshots__/test_tui_snapshots/
git commit -m "feat(jobs): AgentDashboard screen + J navigation (dashboard P1b)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: P1c — command-first input rail (ops verbs, no session)

**Files:**
- Modify: `harness/tui/screens/agent_dashboard.py` (add the command input + verb dispatch)
- Create: `harness/jobs/commands.py` (parse a command line → an `ops` call; pure/testable)
- Create: `tests/test_jobs_commands.py`
- Modify: `tests/test_agent_dashboard.py` (behavior: submit → correct ops call)

**Interfaces:**
- Consumes: `harness.jobs.ops` (`run`, `update`, `remove`), `harness.jobs.view.job_rows`.
- Produces:
  - `harness.jobs.commands.parse_command(line: str) -> tuple[str, str] | None` — returns `(verb, target_name)` for `disable|enable|remove <job name>`, else `None`.
  - `harness.jobs.commands.apply_command(agent_id: str, line: str, now: float) -> str` — resolves the target job by name within the agent, calls the matching `ops` verb, returns a short result string. No agent session.

**Caveman fix — "run" is NOT in the P1 verb set.** `ops.run` requires a live
`executor=` (verified, `ops.py:42`); faking "run now" by setting `next_run_at=now`
would be dishonest (button says run, actually schedules — the exact `/models`-class
dead-end we just fixed). P1 ships only **disable / enable / remove**, which are
honest and need no executor/session. "run now" is added in a later phase when the
dashboard is wired to the real executor the cron daemon uses.

- [ ] **Step 1: Write failing unit tests for command parsing + apply**

Create `tests/test_jobs_commands.py`:

```python
from harness.jobs.commands import parse_command


def test_parse_verbs():
    assert parse_command("disable weekly report cron") == ("disable", "weekly report cron")
    assert parse_command("remove customer import") == ("remove", "customer import")
    assert parse_command("enable nightly sync") == ("enable", "nightly sync")


def test_parse_rejects_unknown_and_deferred_run():
    assert parse_command("please do something") is None
    assert parse_command("") is None
    assert parse_command("run nightly sync") is None   # 'run' deferred (needs executor)
```

- [ ] **Step 2: Run — expect fail**

Run: `../../.venv/bin/python -m pytest tests/test_jobs_commands.py -q`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `harness/jobs/commands.py`**

```python
"""Command-first verbs for the agent dashboard input. Pure parse + a thin apply
over harness.jobs.ops. NO agent session — these mutate scheduled jobs directly."""
from __future__ import annotations

import time

from harness.jobs import ops

# 'run' deliberately excluded in P1: ops.run needs a live executor; faking it via
# next_run_at would be a dishonest "run" (schedules, doesn't run). Deferred.
_VERBS = {"disable", "enable", "remove"}


def parse_command(line: str) -> tuple[str, str] | None:
    parts = line.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    verb, target = parts[0].lower(), parts[1].strip()
    if verb not in _VERBS or not target:
        return None
    return verb, target


def apply_command(agent_id: str, line: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    parsed = parse_command(line)
    if parsed is None:
        return "unrecognized command — try: disable/enable/remove <job name>"
    verb, target = parsed
    jobs = ops.list_jobs(agent_id=agent_id)
    match = next((j for j in jobs if j.name.lower() == target.lower()), None)
    if match is None:
        return f"no job named {target!r} for this agent"
    if verb == "disable":
        ops.update(match.id, now=now, enabled=False); return f"disabled {match.name}"
    if verb == "enable":
        ops.update(match.id, now=now, enabled=True); return f"enabled {match.name}"
    if verb == "remove":
        ops.remove(match.id); return f"removed {match.name}"
    return "unrecognized command"
```

All three P1 verbs use only `ops.update`/`ops.remove` — no executor, no session.
Keep this exactly as shown.

- [ ] **Step 4: Run parse tests green**

Run: `../../.venv/bin/python -m pytest tests/test_jobs_commands.py -q`
Expected: PASS.

- [ ] **Step 5: Add the command input to the screen + a behavior test**

In `agent_dashboard.py`, add an `Input` (id `dashboard-command`) below the table
with placeholder `"disable · enable · remove <job name>"`, and on submit
call `apply_command(self._agent_id, value, now=time.time())`, then re-render the
table via `job_rows` and surface the result string in a `#dashboard-status`
Static. Add to `tests/test_agent_dashboard.py`:

```python
def test_command_disables_job(tmp_path, monkeypatch):
    # seed one enabled job for the default agent, open dashboard, submit
    # "disable <name>", assert ops shows it disabled. Seed via ops.add with a
    # fresh XDG_CONFIG_HOME (mirror the isolated_default_persona fixture).
    ...
```

IMPLEMENTER: write this test concretely — seed a job with `ops.add(...)` under an
isolated `XDG_CONFIG_HOME`, drive the input submit through Pilot, then assert
`ops.list_jobs(agent_id=...)[0].enabled is False`. Use the exact seeding idiom
from existing jobs tests (grep `ops.add(` in `tests/`).

- [ ] **Step 6: Run behavior test green**

Run: `../../.venv/bin/python -m pytest tests/test_agent_dashboard.py -q`
Expected: PASS.

- [ ] **Step 7: Re-baseline the dashboard screen snapshot (now has the input)**

Run: `../../.venv/bin/python -m pytest tests/test_tui_snapshots.py::test_agent_dashboard_screen --snapshot-update -q`
Judge the SVG (input placeholder present, layout balanced) vs the style guide. Re-run without the flag → PASS.

- [ ] **Step 8: Full suite + commit**

Run: `../../.venv/bin/python -m pytest tests/ -q`
Expected: no new failures.

```bash
git add harness/jobs/commands.py harness/tui/screens/agent_dashboard.py \
        tests/test_jobs_commands.py tests/test_agent_dashboard.py \
        tests/test_tui_snapshots.py tests/__snapshots__/test_tui_snapshots/
git commit -m "feat(jobs): command-first dashboard input (dashboard P1c)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** view model → T1; JobsTable + snapshot states → T1 (mixed/empty/scheduled-only; idle == a no-running-rows variant of these); AgentDashboard + J nav → T2; command-first input → T3; progress-None + no-uptime/load + truthful status → T1 constraints + code; catalog entry → T1.9. Chat-free/command-first → T3.
- **Placeholder scan:** the two intentional IMPLEMENTER notes (Job/JobState composition in T1; `ops.run` executor arg in T3) are *verify-against-source* directives with a concrete fallback, not hand-waves — the contracts (assertions, signatures) are fully specified. The T3.5 behavior test is described with an exact assertion + seeding idiom to copy.
- **Type consistency:** `JobRow(name, description, status, progress, when, elapsed)`, `derive_status(job, now)`, `job_rows(agent_id, now)`, `JobsTable.set_rows(rows)`, `AgentDashboard(agent_id, agent_name)`, `action_open_agent_dashboard`, `parse_command`/`apply_command` — used identically across tasks.
- **Known verify-points flagged for the implementer:** `job.state` accessor, `_STATUS_TOKEN` keys resolve to real theme vars, `ops.run` executor arg. Each has a concrete fallback so a task can't stall.
