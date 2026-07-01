# TUI Visual-Snapshot Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `pytest-textual-snapshot` visual regression net over the Done TUI, starting with a deterministic completed-turn-ordering snapshot, and inspect that first render against the written UX standard before baselining.

**Architecture:** Reuse the existing Pilot harness. The TUI is always constructed as a live instance (`HarnessTui(agent_cmd=FAKE_CMD, cwd=..., model="mock")`) with a fake ACP agent subprocess attached — it is never launched from a bare app-file path. Because `pytest-textual-snapshot`'s `snap_compare` historically accepts *either* an app instance or a path, Task 1 verifies which forms this installed version supports and the harness is built against the verified form. Snapshots are captured only after the turn has settled (poll-until-answer-present, the idiom already used across `test_tui_pilot.py`).

**Tech Stack:** Python 3.11, pytest 9.1.1, Textual 8.2.7, `pytest-textual-snapshot` (+ `syrupy`), existing `tests/fake_agent.py` ACP fake.

## Global Constraints

- **Worktree only:** all work happens in `.worktrees/tui-visual-snapshots` on branch `feat/tui-visual-snapshots`. Never edit or commit on `main` (AGENTS.md #1).
- **Python floor:** `requires-python >= 3.11` (pyproject) — do not lower.
- **Textual pin:** `textual>=8,<9` — the plugin must be compatible with 8.2.7.
- **Hermetic tests:** no real LLM, no proxy. Deterministic via XDG isolation + fake agent + fixed `terminal_size=(120, 40)` (per #229 lesson).
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/test_tui_snapshots.py -q`
- **Baseline acceptance rule:** a baseline SVG is committed ONLY after the render is checked against `harness/tui/styles/components.md` + `docs/superpowers/specs/2026-06-26-tui-design-system-design.md`. Never baseline a layout that violates the written standard.
- **Baseline cap:** ≤ ~8 baselines total across the whole backlog.
- **Deterministic persona:** the footer run-caption must resolve to `▣ Bob` via the `_isolated_default_persona` XDG-isolation fixture.
- **App construction (verbatim):** `HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")` where `FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]`.

---

### Task 1: Phase 0 — install plugin and verify `snap_compare` on this box

**Files:**
- Modify: `pyproject.toml:26-31` (add plugin to the `dev` optional-dependencies list)
- Scratch: `/private/tmp/claude-501/-Users-alberto-Work-Quiubo-harness/4fc7723b-1d0a-45a3-8a6e-18442403006b/scratchpad/snap_smoke.py` (throwaway, NOT committed)

**Interfaces:**
- Consumes: nothing.
- Produces: a recorded FACT for Task 2 — whether `snap_compare` accepts an app **instance**, a **path string**, or **both**; and the exact keyword used to run a Pilot script before capture (documented name: `run_before`). This fact selects the harness form in Task 2.

- [ ] **Step 1: Add the plugin to dev deps**

In `pyproject.toml`, inside `[project.optional-dependencies]`'s `dev = [ ... ]` list (currently starts at line 29 with `"textual-dev>=1.7",`), add:

```toml
    "pytest-textual-snapshot>=1.0",
```

- [ ] **Step 2: Install it into the existing venv**

Run: `.venv/bin/python -m pip install "pytest-textual-snapshot>=1.0"`
Expected: installs `pytest-textual-snapshot` and `syrupy`, no dependency conflict against `textual==8.2.7` / `pytest==9.1.1`.

- [ ] **Step 3: Confirm the fixture is registered**

Run: `.venv/bin/python -m pytest --fixtures -q 2>/dev/null | grep -i snap_compare`
Expected: a line naming `snap_compare` (proves the plugin loaded).

- [ ] **Step 4: Read the real signature**

Run: `.venv/bin/python -c "import inspect, pytest_textual_snapshot as p; print([n for n in dir(p)]); import pytest_textual_snapshot as m; src=inspect.getsource(m); print(src[:4000])"`
Record from the source: (a) does the `snap_compare` callable accept an `App` instance, a path, or both? (b) the exact keyword for the pre-capture Pilot coroutine (`run_before`) and for terminal size (`terminal_size`). Write these three facts into a comment at the top of the scratch file in Step 5.

- [ ] **Step 5: Smoke-test an actual SVG on this box**

Create the scratch file `snap_smoke.py` (path above). Use the app-INSTANCE form first (matches how every existing test builds the app):

```python
# FACTS (fill from Task 1 Step 4):
#   snap_compare accepts: <instance | path | both>
#   pre-capture kwarg: run_before
#   size kwarg: terminal_size
import sys
from pathlib import Path
REPO = Path("/Users/alberto/Work/Quiubo/harness/.worktrees/tui-visual-snapshots")
sys.path.insert(0, str(REPO))
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

import pytest
from harness import persona
from harness.tui.app import HarnessTui


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path_factory):
    cfg = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    persona.seed_default_workspace()


def test_smoke(snap_compare):
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
    assert snap_compare(app, terminal_size=(120, 40))
```

- [ ] **Step 6: Run the smoke test (first run writes the baseline)**

Run: `.venv/bin/python -m pytest /private/tmp/claude-501/-Users-alberto-Work-Quiubo-harness/4fc7723b-1d0a-45a3-8a6e-18442403006b/scratchpad/snap_smoke.py -q`
Expected on first run: PASS (a first-run snapshot writes the baseline and passes) OR a clear "snapshot created" message. Confirm an `__snapshots__/` dir with a `.svg` appeared next to the scratch file.

**DECISION BRANCH:**
- If the app-INSTANCE form works → Task 2 uses `snap_compare(app, ...)`. Proceed.
- If it errors that `snap_compare` needs a path → STOP. The instance form is unavailable; Task 2 must instead expose the app via the path form's app-loader convention (a module-level `app` factory the plugin imports). Record the exact error and adjust Task 2's harness to the path form before continuing.

- [ ] **Step 7: Delete the scratch baseline and file; commit only pyproject**

Run: `rm -rf /private/tmp/.../scratchpad/snap_smoke.py /private/tmp/.../scratchpad/__snapshots__`
Then:

```bash
git add pyproject.toml
git commit -m "test(tui): add pytest-textual-snapshot dev dependency (Phase 0)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Expected: 1 file changed. The scratch smoke artifacts are NOT in the repo.

---

### Task 2: Phase 1 — snapshot harness + completed-turn-ordering test

**Files:**
- Create: `tests/tui_snapshot_harness.py`
- Create: `tests/test_tui_snapshots.py`
- Baseline (auto-written by plugin on first run): `tests/__snapshots__/test_tui_snapshots/test_completed_turn_ordering.svg`

**Interfaces:**
- Consumes: `harness.tui.app.HarnessTui`; `harness.persona.seed_default_workspace`; `harness.tui.widgets.prompt_area.PromptArea`; the verified `snap_compare` form from Task 1.
- Produces:
  - `FAKE_CMD: list[str]` — the fake-agent launch command.
  - `REPO: Path` — worktree root.
  - `isolated_default_persona` — a pytest fixture (autouse in the test module) doing XDG isolation + `seed_default_workspace()`.
  - `async def drive_completed_turn(pilot, app, prompt: str) -> None` — sends the first prompt from the landing box and polls `pilot.pause()` until the transcript exists and the answer widget has settled, so a capture taken afterward is stable.

- [ ] **Step 1: Write the harness module**

Create `tests/tui_snapshot_harness.py`:

```python
"""Deterministic boot + drive helpers for TUI snapshot tests.

Single place that knows how to bring HarnessTui to a known VISUAL state for
`snap_compare`. Mirrors the proven idiom in tests/test_tui_pilot.py: construct a
live app with the fake-agent subprocess attached, then poll pilot.pause() until
the turn has settled (this codebase waits on a settled condition, not a
TurnEnded event object)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


@pytest.fixture
def isolated_default_persona(monkeypatch, tmp_path_factory):
    """XDG isolation so the footer run-caption is a deterministic '▣ Bob' on any
    box, independent of the developer's real ~/.config."""
    cfg = tmp_path_factory.mktemp("xdg_config")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    from harness import persona
    persona.seed_default_workspace()


async def drive_completed_turn(pilot, app, prompt: str) -> None:
    """Landing -> conversation, send `prompt`, wait until the answer has settled.

    Capture only AFTER this returns, so the SVG is a frozen completed turn."""
    from harness.tui.widgets.prompt_area import PromptArea
    from textual.containers import VerticalScroll
    from textual.widgets import Markdown

    app.query_one("#landing-input", PromptArea).focus()
    app.query_one("#landing-input", PromptArea).value = prompt
    await pilot.press("enter")

    # 1) wait for the conversation view to exist (transition happened)
    for _ in range(50):
        await pilot.pause()
        if getattr(app, "_started", False) and app.query("#transcript"):
            break

    # 2) wait for the streamed answer to be present AND stable across two ticks
    prev = None
    stable = 0
    for _ in range(80):
        await pilot.pause()
        try:
            scroll = app.query_one("#transcript", VerticalScroll)
        except Exception:
            continue
        mds = [w for w in scroll.children if isinstance(w, Markdown)]
        cur = "".join(
            (getattr(m, "source", None) or getattr(m, "_markdown", "") or "")
            for m in mds
        )
        if cur and cur == prev:
            stable += 1
            if stable >= 2:      # unchanged for two consecutive ticks => settled
                break
        else:
            stable = 0
        prev = cur

    await pilot.pause()          # final drain before the caller captures
```

- [ ] **Step 2: Write the failing snapshot test**

Create `tests/test_tui_snapshots.py`. Use the INSTANCE form verified in Task 1 (if Task 1's branch selected the path form, adapt per the recorded convention):

```python
"""Visual-snapshot regression tests for the Done TUI.

First target: completed-turn ORDERING (prompt -> answer -> footer). This is the
#138 / #81 / #97 / #100 bug class, invisible to state-only Pilot tests."""
from __future__ import annotations

from tests.tui_snapshot_harness import (
    FAKE_CMD,
    REPO,
    isolated_default_persona,   # noqa: F401  (used as an autouse fixture below)
    drive_completed_turn,
)

import pytest
from harness.tui.app import HarnessTui


@pytest.fixture(autouse=True)
def _iso(isolated_default_persona):   # activate XDG isolation for every test here
    yield


def test_completed_turn_ordering(snap_compare):
    """One full turn, captured after it settles. Locks prompt->answer->footer
    order and spacing."""
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")

    async def run_before(pilot):
        await drive_completed_turn(pilot, app, "hello there")

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))
```

- [ ] **Step 3: Run — expect first-run baseline creation**

Run: `.venv/bin/python -m pytest tests/test_tui_snapshots.py -q`
Expected: on the FIRST run the plugin reports the snapshot did not exist and CREATES it (pytest-textual-snapshot fails the first run by design, writing the baseline). Confirm `tests/__snapshots__/test_tui_snapshots/test_completed_turn_ordering.svg` now exists.

- [ ] **Step 4: STOP for the Phase-2 review gate (do NOT commit the baseline yet)**

Do not `git add` the `.svg`. The baseline acceptance rule (Global Constraints) forbids committing a baseline before it is judged against the written standard. Proceed to Task 3. The harness + test source MAY be committed now (they are correct regardless of the render verdict):

```bash
git add tests/tui_snapshot_harness.py tests/test_tui_snapshots.py
git commit -m "test(tui): snapshot harness + completed-turn-ordering test (Phase 1)

Reuses the fake-agent Pilot idiom; captures only after the turn settles.
Baseline SVG intentionally NOT committed yet — pending Phase 2 standard-check.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Phase 2 — judge the render against the written standard, then decide

**Files:**
- Inspect (read-only): `tests/__snapshots__/test_tui_snapshots/test_completed_turn_ordering.svg`
- Standard refs (read-only): `harness/tui/styles/components.md`, `docs/superpowers/specs/2026-06-26-tui-design-system-design.md`
- Possibly modify (only if a bug is found): the offending TUI source under `harness/tui/` (e.g. `harness/tui/app.py` for the #138 append-below-footer path).

**Interfaces:**
- Consumes: the baseline SVG from Task 2; the written standard docs.
- Produces: a recorded VERDICT — `correct` (baseline accepted) or `defective` (bug fixed, then baseline). Either way, the standard-check is written into the commit message.

- [ ] **Step 1: Render the SVG for inspection**

Open the SVG visually (it is a self-contained image). Confirm concretely:
- The user prompt appears ABOVE the agent answer (not the #138 footer-above-answer inversion).
- The answer is a single contiguous block under its own prompt (not the #81 misroute into a prior turn).
- There is vertical spacing between turn elements (not the #97/#100 zero-margin defect).
- The footer run-caption reads `▣ Bob` and sits BELOW the answer.

- [ ] **Step 2: Cross-check against the written standard**

Read `harness/tui/styles/components.md` for the turn/footer components and the design-system spec for spacing/ordering rules. For each of the four checks in Step 1, note whether the render matches the documented intent. This is the acceptance gate — an eyeball alone is not sufficient.

- [ ] **Step 3a: IF render is correct — accept the baseline**

```bash
git add tests/__snapshots__/test_tui_snapshots/test_completed_turn_ordering.svg
git commit -m "test(tui): baseline completed-turn ordering — verified vs UX standard

Render checked against components.md + tui-design-system spec: prompt above
answer, single contiguous answer block, inter-turn spacing present, '▣ Bob'
footer below the answer. Regression net locked in.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Then confirm the test is now GREEN (baseline matches on a clean run):
Run: `.venv/bin/python -m pytest tests/test_tui_snapshots.py -q`
Expected: PASS.

- [ ] **Step 3b: IF render is defective — fix, re-verify, THEN baseline**

Use `superpowers:systematic-debugging` to find the root cause in `harness/tui/` (do not chase theories from the static SVG — confirm against the running app per the debugging rule). Apply the minimal fix. Re-run:

Run: `.venv/bin/python -m pytest tests/test_tui_snapshots.py --snapshot-update -q`
Expected: baseline re-written to the corrected render. Re-open the SVG and repeat Step 1 + Step 2 checks. Only when the corrected render matches the standard:

```bash
git add harness/tui/<fixed-file> tests/__snapshots__/test_tui_snapshots/test_completed_turn_ordering.svg
git commit -m "fix(tui): <describe the layout bug> + baseline the corrected render

Root cause: <one line>. Corrected render verified vs components.md +
tui-design-system spec before baselining.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Full-suite regression check**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: no NEW failures introduced by this work (the snapshot test passes; pre-existing unrelated failures, if any, are unchanged). Record the before/after counts.

---

## Backlog (later phases — not this plan)

Ranked by regression value; each is its own future plan/task:
2. Landing/header — model·provider line (#124), persona indicator.
3. Tool-call row block rendering.
4. Streaming mid-flight + sticky-scroll (#240).
5. Persona rail/drawer (#78).
6. Permission / decision / select modals.
7. `scripts/ux_survey.py` — Goal 3 on-demand UX-survey artifact tool.

## Self-review notes

- **Spec coverage:** Goal 1 (regression net) → Task 2/3a. Goal 2 (fix+lock) → Task 3b. Goal 3 (UX survey) → deferred, in Backlog #7 (matches spec Non-goals). Caveman fix #1 → Task 1 (verify + decision branch). Fix #2 → `drive_completed_turn` settle-poll + capture-after. Fix #3 → Task 3 Step 2 acceptance gate + Baseline acceptance rule. Fix #4 → Backlog #7. Fix #5 → Baseline cap + Textual-upgrade note in constraints.
- **Placeholder scan:** none — every code step shows complete code; the only intentional branch is Task 1's verified decision and Task 3's correct/defective fork, both fully specified.
- **Type/name consistency:** `FAKE_CMD`, `REPO`, `isolated_default_persona`, `drive_completed_turn`, `snap_compare(app, run_before=..., terminal_size=...)` used identically across Tasks 2 and 3.
