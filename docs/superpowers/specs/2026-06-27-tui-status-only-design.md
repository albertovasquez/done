# TUI ActivityRegion — status line only (no command list by default)

**Date:** 2026-06-27
**Status:** Approved (brainstorm)
**Supersedes the default-view direction of:** PR #44 (TaskTree command summary)

## Problem

Every attempt to render tool commands in the default ActivityRegion checklist
(`TaskTree`) hits a new shell-shape it summarizes wrong (`head -40 <longpath>`
overflows; `for f in …; do …; done` collapses to a useless `for f`). The
per-command heuristic is whack-a-mole. The command text is the liability.

## Decision

The default working view shows **only the status line** — no per-command list.
The status line already carries the signal (`◐ <label>… (elapsed · ↓ tokens)`);
we add a completed-tool count. Tools remain reachable on demand via `Ctrl+O`.

1. **Default view (`_details == False`):** status line only. The `TaskTree` is
   never displayed; `update_tasks` is no longer called.
2. **Status line:** append `· N done` when ≥1 tool has completed, where N counts
   `snap.tools` with `status == ToolStatus.DONE`. Result:
   `◐ Running tool… · 4 done (1m 18s · ↓ 4.0k tokens)`.
3. **`Ctrl+O` view (`_details == True`):** unchanged — the per-tool `ToolCallRow`
   list (PR #43) stays as the opt-in escape hatch.
4. **Remove dead code:** delete `summarize_command` + its helpers
   (`_strip_tail`, `_first_quoted`, `_summarize_segment`, `_cap`, constants) and
   their 10 tests; revert `TaskTree.lines_for` to its pre-#44 simple form.
   `TaskTree` stays a valid (if undisplayed) widget — no churn removing it.

## Non-goals

- No removal of `Ctrl+O` / `ToolCallRow` (PR #43 stays).
- No removal of the `TaskTree` widget class itself (only its summarizer + its
  display in the default branch).
- No change to state (`AgentSnapshot`/`ToolView`/`TaskItem`).

## Design

### `activity_status.py`
Add a count to `line_for`. Count completed tools from the snapshot:

```python
done = sum(1 for t in snap.tools if t.status == ToolStatus.DONE)
```

Insert `· {done} done` into the meta segment when `done > 0`. Import
`ToolStatus` from `harness.tui.state`. Pure/display-only; tested via `line_for`.

### `activity_region.py`
In `update_from`, the non-details branch stops showing/updating the TaskTree:

```python
show_tools = self._details and bool(snap.tools)
task_tree.display = False                 # never shown in default view
tools_container.display = show_tools
if show_tools:
    tools_container.remove_children()
    for tv in snap.tools:
        tools_container.mount(ToolCallRow(tv, expanded=False))
```

(The `task_tree.update_tasks(snap.tasks)` call is removed — that was the only
caller of `summarize_command`.) Update the module docstring: default view is the
status line; `ctrl+o` shows the per-tool list.

### `task_tree.py`
Remove the summarizer block (added in #44) and restore `lines_for`:

```python
def lines_for(self, tasks):
    out = []
    for t in tasks:
        glyph, token = _GLYPH.get(t.status, ("□", "muted"))
        label = t.label[2:] if t.label.startswith("$ ") else t.label
        out.append(f"[${token}]{glyph}[/] [$foreground]{label}[/]")
    return out
```

Drop the `import shlex` and the `_NOISE/_SEARCH/_WIDTH_CAP/_PATTERN_CAP`
constants and helper functions.

## Testing (TDD)

1. **`activity_status` count:** `line_for` on a snapshot with 2 DONE + 1 ACTIVE
   tool contains `2 done`; a snapshot with 0 DONE tools does NOT contain `done`.
2. **`activity_region` default view hides commands:** after `update_from` on a
   working snapshot (not details), `TaskTree` is `display == False` (no command
   text shown). After `toggle_details`, `ToolCallRow`s appear. (Update the
   existing `test_activity_region_mounts_and_shows_tool_when_working`: the
   "collapsed → TaskTree present" assertion becomes "TaskTree hidden".)
3. **Remove** the 10 `test_summarize_*` tests and the
   `test_lines_for_uses_summary_and_muted_count` test.
4. Full suite green.

## Files

- `harness/tui/widgets/activity_status.py` — add `· N done` count.
- `harness/tui/widgets/activity_region.py` — hide TaskTree in default branch.
- `harness/tui/widgets/task_tree.py` — remove summarizer, revert `lines_for`.
- `tests/test_tui_widgets.py` — add count test, update region test, remove
  summarizer tests.

No new files, no state changes, no dependency changes.
