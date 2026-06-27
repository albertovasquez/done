# TUI plan checklist — design

**Date:** 2026-06-27
**Status:** Design (approved in brainstorming; pending spec review)
**Branch / worktree:** `worktree-tasktree-plan`

## Summary

Surface a live, model-authored **plan checklist** in the pinned `ActivityRegion`
above the composer: the agent declares the steps of multi-step work up front, the
steps tick off in place as they complete, and the checklist clears when the turn
ends. This is the bottom-of-screen "current work" indicator the user screenshotted
(`▣ Push + PR / □ CI + merge / □ Sync + prune`).

The TUI half is **mostly already built** — the `TaskTree` widget, its
done/in-progress/pending glyphs, the in-place status update, the pinned region,
and the hide-when-idle behavior all exist (PRs #43, #44). What is missing is a
*data source*: today the only producer of checklist items is the tool-call
reducer, so the checklist (if shown) would list raw tool invocations, not a
readable plan. This design adds the missing source by wiring ACP's **native
`plan` update** — which `render.py` currently deliberately drops — through the
reducer into the existing widget.

## Decisions (locked in brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Plan source | **Model-authored plan** | Matches Claude Code (`TodoWrite`) / Codex (`update_plan`); clean semantic labels, not raw tool calls. |
| Mechanism | **ACP native `plan` update** | ACP already defines `AgentPlanUpdate` / `PlanEntry`; no bespoke tool needed. `render.py:61` already has the forward-compat seam. |
| Lifetime | **Session-transient (RAM only)** | Like Claude Code/Codex. No disk clutter, no race with the persona-memory feature. |
| Trigger | **Agent decides via system prompt** | Agent judges when work is multi-step; single-step turns show no checklist. |
| Field model | **Separate `plan` field** | `plan` (replace-semantics, plan-authored) is distinct from `tasks` (append, tool-authored). No writer conflict; leaves the tool reducer untouched. |
| Coverage | **Ad-hoc agent turns only** | Explicit `/ship`-style emission deferred (YAGNI). |

## Non-goals

- **No file persistence.** The plan lives only in `AgentSnapshot`; it is not
  written to the workspace and is unrelated to the persona-memory system.
- **No bespoke plan tool.** ACP's native `update_plan` is the channel.
- **No `/ship`-style explicit emission** in this iteration. A reusable
  flow-helper can be added later without changing the data model.
- **No priority rendering.** ACP `PlanEntry.priority` (high/medium/low) is
  ignored; we render content + status only.
- **No tool-reducer changes.** `tasks`/`tools` keep their current append
  behavior, byte-for-byte.

## The ACP contract (verified against installed `acp`)

```python
# acp helpers (already in the installed package)
update_plan(entries) -> AgentPlanUpdate     # session_update="plan", entries=list(entries)
plan_entry(content, *, priority="medium", status="pending") -> PlanEntry
# PlanEntry: content:str, priority: high|medium|low, status: pending|in_progress|completed
```

Key property: **a `plan` update is a full snapshot, not a delta.** Each
`update_plan(...)` replaces the entire plan. The agent re-emits the complete list
with updated statuses as it progresses (exactly like `TodoWrite`). This is why
the `plan` field uses replace-semantics, in contrast to the append-only `tasks`.

ACP plan status has **no `failed` state** — plan items render only
pending / in_progress / done. Failures continue to surface via the tool path and
the status line.

## Architecture & data flow

```
Agent loop                       TUI pure core                    Widget
──────────                       ─────────────                    ──────
update_plan([                    render_update(AgentPlanUpdate)
  plan_entry("Push + PR",   ──►    → RenderedItem(kind="plan", ──► reduce(PlanUpdated)
            status="in_progress"),     entries=(...))                → snapshot.plan = (…)  ──► TaskTree.update_tasks(snap.plan)
  plan_entry("CI + merge"),
  ...])
session_update(sid, ·)           [render.py]                      [state.py]                  [activity_region.py]
```

Same emit channel the agent already uses for messages and tool calls
(`self._conn.session_update(session_id, <update>)`).

## Components

Four small, independently testable changes. Each maps to an existing seam.

### 1. `harness/tui/render.py` — render the plan update

Today `render_update` returns `None` for plan updates (line 61, comment:
`"plan, current_mode_update, etc. — forward-compat"`). Add a branch:

- Detect the ACP plan update (`type(update).__name__ == "AgentPlanUpdate"`, or
  `getattr(update, "session_update", "") == "plan"` — match the duck-typing style
  already used in this file).
- Return a `RenderedItem` carrying the entries. Add `kind="plan"` and a field to
  hold the entries as a tuple of `(content, status)` pairs (e.g. extend
  `RenderedItem` with `entries: tuple[tuple[str, str], ...] = ()`).
- Pure; tested with plain stubs like the rest of `render_update`.

### 2. `harness/tui/state.py` — `PlanUpdated` event, replace-semantics

- New `AgentSnapshot.plan: tuple[TaskItem, ...] = ()` field (default empty).
- New frozen event `PlanUpdated(entries: tuple[tuple[str, str], ...])`.
- Reducer maps each entry → `TaskItem(label=content, status=<mapped>,
  tool_id="")` and **replaces** `a.plan` wholesale. Status map:
  `pending→pending`, `in_progress→in_progress`, `completed→done`.
- `TurnStarted` already resets transient state — add `plan=()` to its `replace(...)`
  so the checklist clears at turn start (and thus at the end of the previous turn).
- The `ItemReceived` handling for `kind="plan"` dispatches to the same
  replace logic (or the app translates a `kind="plan"` RenderedItem into a
  `PlanUpdated` event before calling `reduce` — pick whichever matches the existing
  app→reduce wiring; see "Open implementation choice" below).
- **`tasks`/`tools` and their tool-call append logic are untouched.**

### 3. `harness/tui/widgets/activity_region.py` — show the checklist when a plan exists

Today `task_tree.display = False` is hard-coded (line 70). Change to:

- `has_plan = bool(snap.plan)`
- `task_tree.display = has_plan`
- when shown: `task_tree.update_tasks(snap.plan)`
- Tools stay behind `ctrl+o` exactly as today (the `show_tools` path is unchanged).

A turn with no plan is visually identical to today (status line only). A turn
with a plan shows the checklist below the status line.

### 4. System prompt — teach the agent to emit a plan

Add one instruction block to the agent system prompt (the same prompt the
persona/skills injection composes):

> For multi-step work, emit a plan up front using the plan update with one entry
> per step. Re-emit the **full** plan with updated statuses as you start and
> finish each step (mark the active step `in_progress`, completed steps
> `completed`). Skip the plan for single-step or trivial work.

Wording to be finalized against the existing prompt's voice during implementation.

## Open implementation choice (resolve during planning, not now)

`render.py` produces `RenderedItem`s; `state.py` consumes either `RenderedItem`s
(via `ItemReceived`) or dedicated events. The existing reducer handles tools
through `ItemReceived` with `kind="tool"`. Two consistent options:

1. Handle `kind="plan"` inside `ItemReceived` (symmetric with `kind="tool"`).
2. Emit a dedicated `PlanUpdated` event from the app layer after `render_update`
   returns a `kind="plan"` item.

Prefer whichever matches how the app currently turns `RenderedItem`s into reducer
calls. This is a wiring detail, not a design fork — both yield the same snapshot.

## Error handling & edge cases

- **Empty plan (`update_plan([])`):** `plan=()` → checklist hidden. Valid way for
  the agent to clear a plan mid-turn.
- **Malformed entry:** tolerate missing `content`/`status` like `render.py`'s
  existing `getattr(..., "")` style; unknown status falls back to `pending`.
- **Plan + tools in the same turn:** independent fields, no interaction. Plan shows
  in the checklist; tools show in the `ctrl+o` detail view.
- **Idle/terminal:** `ActivityRegion.update_from` already hides the whole region
  when idle, which hides the checklist too.

## Testing

Pure-core units (no Textual, no async), in the style of the existing
`render.py`/`state.py` tests:

- `render.py`: an `AgentPlanUpdate`-shaped stub → `RenderedItem(kind="plan",
  entries=...)`; non-plan updates still return `None`.
- `state.py`: `PlanUpdated` replaces `plan`; re-emit updates statuses in place;
  status mapping (pending/in_progress/completed); `TurnStarted` clears `plan`;
  tool-call append to `tasks` is unaffected by a plan update.
- `activity_region.py` (widget-level / snapshot-driven): plan present → TaskTree
  displayed with the right lines; no plan → TaskTree hidden; tools still gated on
  `ctrl+o`.

Run from the worktree root: `.venv/bin/python -m pytest tests/ -q`
(note: the venv lives in the primary checkout; run with the worktree as cwd per
the editable-install shadowing lesson).

## Risk & rollback

- **Lowest-risk path:** the tool reducer and its tests are untouched; the new
  `plan` field is additive and defaults empty, so existing behavior is preserved
  when no plan is emitted.
- **Rollback:** revert the four changes; the additive `plan` field and the
  forward-compat `render.py` seam mean nothing else depends on them.
