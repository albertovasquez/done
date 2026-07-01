# TUI visual-snapshot testing

State-based Pilot tests (`tests/test_tui_*.py`) assert on *widget state* — this
widget exists, has this text, this class. They are blind to *rendered layout*, so
a whole class of TUI bugs slips past them: a footer rendering above the answer,
zero inter-turn spacing, a misrouted stream. Those are the recurring bugs in this
project's history (#138, #81, #97/#100, #124, #240).

Visual-snapshot testing closes that gap. `pytest-textual-snapshot` boots the real
`HarnessTui` headless, drives it with a Pilot script, renders the terminal to an
**SVG**, and diffs that SVG against a committed baseline. A layout change that
Pilot can't see becomes a red test.

> This net already earned its keep: the first snapshot test caught a live
> footer-above-answer bug in the non-streaming answer path that all ~1500
> state-based tests missed (fixed in the same branch —
> `_append_streaming_below_footer`).

- **Design & rationale:** `docs/superpowers/specs/2026-07-01-tui-visual-snapshot-testing-design.md`
- **Plan / task breakdown:** `docs/superpowers/plans/2026-07-01-tui-visual-snapshot-testing.md`

## Running the tests

```bash
.venv/bin/python -m pytest tests/test_tui_snapshots.py -q
```

A clean run compares against the committed baseline SVGs under
`tests/__snapshots__/`. On a mismatch the plugin drops a `snapshot_report.html`
into the working directory with a **before/after side-by-side** — open it to see
exactly what moved. (That report file is git-ignored.)

## How a test is wired

Two pieces, deliberately separated:

- **`tests/tui_snapshot_harness.py`** — the single place that boots the app
  deterministically. It provides `REPO`, `FAKE_CMD`, the `isolated_default_persona`
  fixture (XDG isolation so the footer caption resolves to `▣ Bob` on any box),
  and `drive_completed_turn(pilot, app, prompt)`.
- **`tests/test_tui_snapshots.py`** — the tests themselves: thin
  `assert snap_compare(app, run_before=..., terminal_size=(120, 40))` calls.

The app is always constructed as a live instance with the fake ACP agent
attached, exactly as the other Pilot tests do:

```python
app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
```

The `snap_compare` fixture accepts that instance directly (it takes
`str | PurePath | App`), so the fake-agent subprocess drives the render — no real
LLM, no proxy.

### Capture only after the turn has settled

A snapshot is a frozen picture; capturing mid-stream is flaky. `drive_completed_turn`
returns only once **both** the streamed answer text is stable across two ticks
**and** the turn has reached a terminal `AgentState` (`DONE`/`FAILED`). If the app
never reaches that state within the budget, the helper **raises** rather than
returning — a silent early return would bake a garbage baseline (e.g. the landing
screen) that then "passes" forever. When you add a test, drive the app to a
settled state the same way; never `snap_compare` on a mid-flight frame.

## Adding a UX-focused test (for agents)

You changed TUI UI (a component, a layout, a chip, a modal). Add a test that
would catch it breaking. Use this decision rule:

| What you changed | Reach for | Why |
|---|---|---|
| **Layout** — ordering, spacing, footer/chip position, wrapping, alignment | **snapshot** (`tests/test_tui_snapshots.py`) | Pilot can't see pixels; layout bugs are exactly what it misses. |
| **Behavior / state** — a widget appears, has text/class, reacts to a key/click | **Pilot** (`tests/test_tui_*.py`, e.g. `test_tui_pilot.py`) | Assert on widget state directly; faster, no baseline to maintain. |
| **Both** (usually) | one of each | The snapshot locks the look; the Pilot test locks the wiring. |

**Reusable harness — build on these, don't reinvent:**

- `tests/tui_snapshot_harness.py` — `REPO`, `FAKE_CMD`, the
  `isolated_default_persona` fixture (deterministic `▣ Bob` caption), and
  `drive_completed_turn(pilot, app, prompt)` (drives one turn, returns only after
  it settles). Reuse the driver; write a new settle-aware one only for a new shape
  (modal, tool-call row, rail).
- `tests/fake_agent.py` — the ACP fake. A plain prompt yields the answer `done`;
  keyword prompts drive shapes: `STREAM` (multi-delta answer), `PERMISSION`
  (permission modal), `TRACE`, `BURST`, `SLOW`, `MANYCHUNKS`. Pick the keyword
  that reproduces the UI state you changed.
- `tests/test_tui_pilot.py` — the Pilot idioms to copy: `_send_first_prompt`,
  `_transcript_text`, `_footer`, and the `for _ in range(N): await pilot.pause()`
  settle loop.

**Copy-paste snapshot template** (a completed-turn layout):

```python
from tests.tui_snapshot_harness import (
    FAKE_CMD, REPO, isolated_default_persona, drive_completed_turn,  # noqa: F401
)
import pytest
from harness.tui.app import HarnessTui


@pytest.fixture(autouse=True)
def _iso(isolated_default_persona):
    yield


def test_<the_layout_you_changed>(snap_compare):
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")

    async def run_before(pilot):
        await drive_completed_turn(pilot, app, "hello there")
        # …drive to the exact UI state you changed (open a modal, run a tool, etc.)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))
```

Then generate the baseline (`--snapshot-update`) and **judge it against the style
guide before committing** — see *The baseline acceptance rule* below. Never commit
a baseline of a layout that violates `components.md` / the design-system spec; fix
the UI first.

## Adding a new snapshot test

1. Add a `run_before(pilot)` coroutine that drives the app to the state you want
   frozen (reuse `drive_completed_turn`, or write a settle-aware driver for a new
   shape — modal, tool-call row, persona rail).
2. Write `assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))`.
3. Generate the baseline: `pytest tests/test_tui_snapshots.py -q --snapshot-update`.
4. **Judge the baseline before committing it** (see below). Only then `git add`
   the `.svg`.

## The baseline acceptance rule

A snapshot freezes *whatever* renders — it can't tell a good layout from a bug.
Committing a baseline of a subtly-wrong layout cements the bug and makes the test
*defend* it. So a baseline SVG is committed **only after** its render is checked
against the written UX standard:

- `harness/tui/styles/components.md` — the component catalog / design system
- `docs/superpowers/specs/2026-06-26-tui-design-system-design.md` — spacing,
  ordering, and grammar rules

If the render violates the standard, fix the UI first, re-generate, re-check —
then baseline the corrected render.

## Re-baselining (intended changes & version bumps)

Baselines are sensitive to Textual version bumps and theme/token edits — a
`textual` major bump churns every SVG at once. When a change to the render is
*intended*:

```bash
.venv/bin/python -m pytest tests/test_tui_snapshots.py --snapshot-update -q
```

Then **review the `snapshot_report.html` diff** and confirm the new render still
matches the written standard before committing the updated `.svg`. Never
auto-update baselines as a side effect of an unrelated change, and treat a Textual
upgrade as a deliberate re-baseline pass in its own PR. Keep the total baseline
count small (≈8) — if a layout needs many variants, it's probably more than one
test.

## Toolchain note: pytest is capped at `<9`

`pytest-textual-snapshot` pulls in `syrupy`, which pins `pytest<9`. The shared
test venv therefore runs pytest 8.x (the full suite is green on it; no pytest-9
features are in use). This is pinned in `pyproject.toml`'s `dev` extra.

## UX-survey backlog (Goal 3)

Beyond regression-catching, these SVGs are meant to support periodic **UX
surveys** — rendering the current layouts and critiquing them against the style
guide on demand. The on-demand survey tool (`scripts/ux_survey.py`) is not built
yet; it and the next snapshot targets are tracked in the plan's backlog:

1. Completed-turn ordering — **done** (this branch)
2. Landing / header — model·provider line (#124), persona indicator
3. Tool-call row block rendering
4. Streaming mid-flight + sticky-scroll (#240)
5. Persona rail / drawer (#78)
6. Permission / decision / select modals
7. `scripts/ux_survey.py` — the on-demand UX-survey artifact tool
