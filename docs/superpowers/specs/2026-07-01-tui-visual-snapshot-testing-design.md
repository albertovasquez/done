# TUI Visual-Snapshot Testing + UX-Survey Harness — Design

**Date:** 2026-07-01
**Status:** Approved design, pre-implementation
**Branch/worktree:** `feat/tui-visual-snapshots` @ `.worktrees/tui-visual-snapshots`

## Problem

Done's TUI (`HarnessTui`, Textual 8.2.7) has a recurring class of *visual layout*
regressions that the existing Pilot tests (`test_tui_pilot.py` et al.) cannot
catch. Pilot asserts on **widget state** (this widget exists, has this text) — it
is blind to **rendered layout**. The bug history is dominated by exactly the
layout failures Pilot misses:

- #138 — footer rendered *above* the answer; trailing deltas landed after it.
- #81 — streamed prose misrouted under the wrong prompt turn.
- #97 / #100 — turn elements had no vertical spacing.
- #124 — model·provider line placement under the header rule.
- #240 — sticky-scroll yanked the view to bottom mid-read.

Each of these is a *visual* defect. We want a mechanical net for them, a way to
fix-and-lock known ones, and — separately — a way to periodically survey the
live app against our written UX standard.

## Goals

1. **Regression net** — freeze current visual layout so future changes cannot
   silently break it.
2. **Fix + lock known bugs** — where the current render is wrong against the
   written standard, fix it, *then* baseline (never baseline a bug).
3. **UX surveys against the style guide** — a deliberate, on-demand act (not the
   CI lane) that renders current layouts to inspectable artifacts and critiques
   them against the written UX standard.

## Non-goals

- CI GitHub Actions wiring (later phase).
- The `scripts/ux_survey.py` artifact tool (deferred — see Phasing).
- Any snapshot beyond the **completed-turn-ordering** test this phase.

## The written UX standard (what "the style guide" is)

Already exists in-repo; snapshot review judges renders against **these**, not a
subjective eyeball:

- `harness/tui/styles/components.md` — per-component "When to use" guidance.
- `harness/tui/styles/brandbook.py` — live-token brand book (already tested by
  `tests/test_tui_brandbook.py`).
- `harness/tui/tokens.py`, `harness/tui/theme.py` — the design tokens.
- `docs/superpowers/specs/2026-06-26-tui-design-system-design.md` — the design
  system spec.

## Tool choice

**`pytest-textual-snapshot`** (Textualize's official plugin; pulls in `syrupy`).
Boots the app headless, runs a Pilot script, renders the terminal to **SVG**,
diffs against a committed baseline, and emits an HTML before/after report on
mismatch.

**Why this and not alternatives:**

- We are already on Textual 8.2.7 + pytest 9.1.1 with a working Pilot harness and
  a real ACP fake (`tests/fake_agent.py`). Snapshot testing reuses all of it.
- Generic terminal-capture harnesses (`pyte`, tmux) would re-implement that and
  drag in the two-process flakiness fought in #216/#229/#99.

**Accepted tax — baseline maintenance:** each baseline is a multi-KB SVG blob in
git; a Textual major bump (8→9) or theme edit churns *all* baselines at once.
Policy (see Baseline Policy) caps the count and gates Textual upgrades on a
deliberate re-baseline pass.

## Caveman-review fixes folded into this design

This design was adversarially reviewed. Five load-bearing corrections:

1. **Verify before design (Step 0).** Two claims were unverified and load-bearing:
   (a) that `snap_compare` accepts a constructed `App` *instance* (vs. only a path
   to an app file), and (b) that live two-process streaming snapshots are stable.
   Nothing downstream is written on faith — Step 0 proves the tool works on this
   box and pins the real `snap_compare` signature before the harness is designed
   against it.
2. **Capture only after a confirmed terminal state.** Snapshotting a live
   subprocess stream invites the timing nondeterminism of #216/#229/#99. We
   capture only after **TurnEnded** (the reliable terminal signal per #99). If
   the subprocess path still proves flaky, **direct event-injection** (post
   Textual messages for a frozen transcript) is the sanctioned fallback — not
   dismissed.
3. **Judge against the written standard before baselining.** A snapshot freezes
   *whatever* renders; baselining a subtly-wrong layout cements the bug and makes
   the test *defend* it. Before accepting any baseline, the render is checked
   against `components.md` + the design-system spec — not just "looks plausible."
4. **Cut `ux_survey.py` from this phase.** Goal 3's artifact tool is not required
   to ship the first test. Deferred (see Phasing).
5. **Name the baseline-maintenance policy** — count cap + Textual-upgrade
   re-baseline gate (see Baseline Policy).

## Architecture

Two units this phase (a third is deferred):

### 1. `tests/tui_snapshot_harness.py` (new)

The single place that knows how to boot the app deterministically for a snapshot.
Extracts the reusable pieces currently inline in `test_tui_pilot.py`:

- `_isolated_default_persona` — XDG isolation so the footer caption resolves to
  the shipped "Bob" name on any box.
- fake-agent subprocess wiring (`tests/fake_agent.py`).
- `_send_first_prompt` — landing → conversation transition.
- a `run_before` builder for **completed-turn** state that waits for **TurnEnded**
  before returning (fix #2).

*What it does:* produce a driven app frozen at a known visual state.
*How you use it:* import its `run_before` builder into a snapshot test.
*Depends on:* `harness.tui.app.HarnessTui`, `tests/fake_agent.py`.

### 2. `tests/test_tui_snapshots.py` (new)

Thin snapshot tests. First and only test this phase: **completed-turn ordering** —
one full turn (prompt → streamed multi-line answer → TurnEnded), asserting the
visual order prompt → answer → footer with correct spacing (the #138/#81/#97/#100
signature).

*Depends on:* the harness (1) + the `snap_compare` fixture.

### 3. `scripts/ux_survey.py` — **DEFERRED** (documented for the backlog)

Standalone command (not the CI lane) that renders the covered layouts to SVGs
under `docs/ux-survey/<date>/` plus a manifest pairing each SVG with the relevant
`components.md` section, so an agent or human critiques current render against the
written standard on demand. Not built this phase.

## Determinism & error handling

- **Hermetic:** XDG isolation + fake agent + fixed `terminal_size=(120, 40)`. No
  real LLM, no proxy (per the #229 hermeticity lesson).
- **Settling:** capture strictly after TurnEnded; `pilot.pause()` to drain the
  event loop before render (fix #2).
- **Flaky fallback:** if the subprocess stream still races the capture,
  switch the first test to direct event-injection for a frozen transcript.

## Baseline Policy (fix #5)

- Baselines are committed SVGs under `tests/__snapshots__/`.
- **Count cap:** ≤ ~8 baselines total across the full backlog; if a layout needs
  more variants, question whether it's really one test.
- **Textual-upgrade gate:** a Textual major/minor bump is a deliberate act that
  includes a full `--snapshot-update` pass with human review of the HTML report
  in the same PR. Baselines are never auto-updated in an unrelated change.
- **Acceptance rule:** a new/changed baseline is only accepted after the render is
  checked against `components.md` + the design-system spec (fix #3).

## Implementation phasing

**Phase 0 — Verify (fix #1).** In the worktree: add `pytest-textual-snapshot` to
the dev/test deps, install, read the real `snap_compare` signature, run a
throwaway one-line smoke snapshot against `HarnessTui`. Confirm an SVG is
produced. *If this fails or the signature differs, revise the harness design
before proceeding.*

**Phase 1 — Harness + first test (this session).** Build
`tests/tui_snapshot_harness.py`, write the completed-turn-ordering test in
`tests/test_tui_snapshots.py`, run it.

**Phase 2 — Bug hunt (this session).** Inspect the produced SVG against the
written standard for the #138/#81/#97/#100 signature.
- Render correct → commit as baseline (regression net locked).
- Render defective → that defect is the "do one" win: fix it, re-verify the SVG
  against the standard, then baseline.

**Later phases (backlog, ranked by regression value):**
2. Landing/header — model·provider line (#124), persona indicator.
3. Tool-call row block rendering.
4. Streaming mid-flight + sticky-scroll (#240).
5. Persona rail/drawer (#78).
6. Permission modal, decision/select modals.
7. `scripts/ux_survey.py` (Goal 3 tool).

## Success criteria

- Phase 0: a smoke `snap_compare` runs and produces an SVG on this box; the real
  fixture signature is recorded in the harness.
- Phase 1: `.venv/bin/python -m pytest tests/test_tui_snapshots.py -q` runs the
  completed-turn test deterministically (green on repeat runs, no flake).
- Phase 2: either a committed baseline reviewed against the written standard, or a
  fixed layout bug + its baseline — with the decision and the standard-check
  recorded.

## Test command

From the worktree root: `.venv/bin/python -m pytest tests/test_tui_snapshots.py -q`
