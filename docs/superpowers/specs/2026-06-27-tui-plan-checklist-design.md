# TUI plan checklist — design

**Date:** 2026-06-27
**Status:** Design (approved in brainstorming; pending spec review)
**Branch / worktree:** `worktree-tasktree-plan`

## Summary

Surface a live, model-authored **plan checklist** in the pinned `ActivityRegion`
above the composer: the agent declares the steps of multi-step work up front, the
steps tick off in place as they complete, and the checklist disappears when the
agent goes idle (data cleared on the next turn start). This is the bottom-of-screen
"current work" indicator the user screenshotted
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

- Detect the ACP plan update with `type(update).__name__ == "AgentPlanUpdate"`.
  This matches how `render_update` already dispatches — by type name (render.py:38),
  **not** by a `session_update` attribute. (Verified: app.py and render.py both
  branch on `type(update).__name__`.)
- Return a `RenderedItem` carrying the entries. Add `kind="plan"` and a field to
  hold the entries as a tuple of `(content, status)` pairs (e.g. extend
  `RenderedItem` with `entries: tuple[tuple[str, str], ...] = ()`).
- Update the `RenderedItem.kind` docstring enumeration (render.py:13) to include
  `"plan"`.
- Pure; tested with plain stubs like the rest of `render_update`.

### 2. `harness/tui/state.py` — `PlanUpdated` event, replace-semantics

- New `AgentSnapshot.plan: tuple[TaskItem, ...] = ()` field (default empty).
- New frozen event `PlanUpdated(entries: tuple[tuple[str, str], ...])`.
- Reducer maps each entry → `TaskItem(label=content, status=<mapped>,
  tool_id="")` and **replaces** `a.plan` wholesale. Status map:
  `pending→pending`, `in_progress→in_progress`, `completed→done`.
- `TurnStarted` resets transient state (`tool`, `decision`, `tasks`, `tools`,
  `elapsed` — state.py:165) — add `plan=()` to its `replace(...)`. **Clearing is
  turn-START-driven, not turn-end-driven:** `TurnEnded` only sets terminal state
  and clears `tool`/`activity_label` (state.py:177); it does not clear `plan`. The
  checklist is *visually hidden* the moment the agent goes idle/terminal (the
  `ActivityRegion` hides the whole region — activity_region.py:42), and the plan
  *data* is cleared on the next `TurnStarted`. This is the correct, race-free
  behavior; the field never needs clearing on `TurnEnded`.
- Plan folding happens **inside `ItemReceived`** for `kind="plan"` (replace
  `a.plan`), symmetric with the existing `kind="tool"` handling. This is the only
  viable route without new wiring: `on_session_update` always converts a non-None
  `RenderedItem` into `ItemReceived(item)` (app.py:847–851) — there is no live path
  that emits a custom event from a rendered item. (See "Resolved: render→reduce
  wiring" below.)
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

## Resolved: render→reduce wiring (no app.py change)

`on_session_update` calls `render_update(msg.update)` and, for any non-None item,
unconditionally folds it via `self._apply(ItemReceived(item))` (app.py:847–851).
There is **no** existing path that translates a `RenderedItem` into a dedicated
event. Therefore plan folding lives **inside the `ItemReceived` reducer branch**
for `kind="plan"` — exactly parallel to the existing `kind="tool"` handling. A
separate `PlanUpdated` event was considered and rejected: it would require adding
a new branch to `on_session_update` (app.py), which this design avoids. Net:
**app.py is not modified.** (This corrects an earlier draft that listed the event
route as an open choice — it is not viable without an unlisted app.py edit.)

## Error handling & edge cases

- **Empty plan (`update_plan([])`):** `plan=()` → checklist hidden. Valid way for
  the agent to clear a plan mid-turn.
- **Malformed entry:** tolerate missing `content`/`status` like `render.py`'s
  existing `getattr(..., "")` style; unknown status falls back to `pending`.
- **Plan + tools in the same turn:** independent fields, no interaction. Plan shows
  in the checklist; tools show in the `ctrl+o` detail view.
- **Idle/terminal:** `ActivityRegion.update_from` already hides the whole region
  when idle (activity_region.py:42), which hides the checklist immediately when the
  turn finishes. The plan *data* persists in `snapshot.plan` until the next
  `TurnStarted` resets it — hidden first, cleared next turn. No flicker, no stale
  display.

## Testing

Pure-core units (no Textual, no async), in the style of the existing
`render.py`/`state.py` tests:

- `render.py`: an `AgentPlanUpdate`-shaped stub → `RenderedItem(kind="plan",
  entries=...)`; non-plan updates still return `None`.
  **Existing test to change:** `tests/test_tui_render.py:57`
  (`test_render_unknown_returns_none`) currently asserts `AgentPlanUpdate` → `None`.
  That assertion becomes false; split it — keep an unknown-update→`None` case with a
  genuinely unknown type, and add the new `AgentPlanUpdate`→`kind="plan"` case.
- `state.py`: `PlanUpdated` replaces `plan`; re-emit updates statuses in place;
  status mapping (pending/in_progress/completed); `TurnStarted` clears `plan`;
  tool-call append to `tasks` is unaffected by a plan update.
- `activity_region.py` (widget-level / snapshot-driven): plan present → TaskTree
  displayed with the right lines; no plan → TaskTree hidden; tools still gated on
  `ctrl+o`.

Run from the worktree root: `.venv/bin/python -m pytest tests/ -q`
(note: the venv lives in the primary checkout; run with the worktree as cwd per
the editable-install shadowing lesson).

## Resolved during implementation: the plan transport (sentinel command)

The spec above assumed the agent could emit a native ACP `plan` update directly.
In implementation this hit a real constraint: the agent is **mini-SWE-agent**
(`minisweagent.agents.default.DefaultAgent`), whose only action channel is
*bash commands* — there is no structured tool-call channel, so the model cannot
call `update_plan(...)`.

**Resolution:** a sentinel `plan` command, mirroring the existing memory
"structured-capability-over-shell" pattern. The agent runs
`plan "label:status" "label:status" …`; `AcpEnvironment.execute` intercepts it via
`parse_plan_command` (pure, in `acp_emit.py`), emits the ACP plan update through an
`on_plan` callback, and returns success **without** running it as a shell command
or asking permission. The base prompt teaches the concrete grammar. Everything
downstream (render → reduce → widget) is exactly as specified above — the sentinel
only fills the missing producer. Status grammar: `pending|in_progress|completed`,
default `pending`, last-colon split so labels may contain colons.

## Risk & rollback

- **Lowest-risk path:** the tool reducer and its tests are untouched; the new
  `plan` field is additive and defaults empty, so existing behavior is preserved
  when no plan is emitted.
- **Rollback:** revert the four changes; the additive `plan` field and the
  forward-compat `render.py` seam mean nothing else depends on them.
