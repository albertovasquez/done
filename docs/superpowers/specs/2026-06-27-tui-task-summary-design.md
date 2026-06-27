# TUI TaskTree — smart command summary

**Date:** 2026-06-27
**Status:** Approved (brainstorm)
**Surface:** `harness/tui/widgets/task_tree.py` (the default, compact ActivityRegion view)

## Problem

While the agent responds, the **default** ActivityRegion view is the `TaskTree`
checklist. Each tool call becomes one line: `glyph + full command`. The command
is printed whole with no length cap (`task_tree.py:29`), so long chained shell
commands (`cd harness && cat persona.py && echo "X" && cat persona_config.py …`)
wrap across many physical lines — a vertical flood that buries the checklist.

This is a *different* surface from the `Ctrl+O` `ToolCallRow` view fixed in
PR #43. That fix stays; this is additive and display-only.

## Goal

Render each task as a short, scannable summary that still conveys *what the
agent did* — DoneDone is a learning tool, so we surface the action, we don't
hide it. Example: `cat persona.py && echo X && cat persona_config.py` →
`✓ cat persona.py  (+1 more)`.

## Non-goals

- No change to `ToolCallRow` / the `Ctrl+O` detail view (PR #43 owns that).
- No change to state. `TaskItem.label` keeps the full command.
- No per-task interactive expand/collapse.
- No perfect shell parsing — a lean heuristic with a safe fallback.

## Design

A pure helper `summarize_command(cmd: str) -> str` in `task_tree.py`, applied to
`t.label` at the single render site (`lines_for`). State is untouched —
`TaskItem` is matched by `tool_id` (state.py:44), so nothing keys off the
displayed text; truncating display-side is safe.

### Heuristic

Operating on the raw command string:

1. **Split the chain on `&&`** into segments (trim whitespace).
2. **Classify each segment.** *Noise* = leading program is one of
   `cd`, `echo`, `ls`, `source`, `export`. Everything else is *real*.
3. **Summarize the first real segment** to `program + first non-flag token`:
   - strip pipes (`| …`) and redirects (`2>/dev/null`, `> x`) before tokenizing;
   - a **quoted argument wins** over flags (for `grep`/`find`/`rg`):
     `grep -rn "system_prompt\|..." *.py` → `grep "system_prompt..."`
     (quoted pattern truncated to a short cap, e.g. 24 chars, with `…`);
   - otherwise first non-`-` token: `git log --oneline -5` → `git log`,
     `cat persona.py` → `cat persona.py`, `python3 -c "..."` → `python3 -c`.
4. **Count the other real segments** → append `[$muted] (+N more)[/]` when N ≥ 1.
5. **Hard width cap** on the final visible text (ellipsis tail) as a backstop so
   no single line can wrap.

### Fallback (safety rule)

If no real segment is found (empty after stripping, or all-noise), fall back to
the **current behavior**: the full label, width-capped. Never blank, never a
misleading summary — degrade to truncated-full-command.

If the *only* segment is noise-classified but it is the sole segment (e.g. a bare
`ls -la`), it is by definition the first real segment in a one-segment chain and
renders as itself (`ls -la`). No special carve-out needed.

### Width cap

The summary (including `(+N more)`) is capped to a fixed character budget with an
ellipsis tail. Fixed budget (not live terminal width) keeps the helper pure and
testable; the ActivityRegion is narrow by design and the summary is short, so a
generous fixed cap (e.g. 60 chars) effectively never triggers except on
pathological single commands.

## Worked examples (from real session screenshots)

| Command | Summary |
|---|---|
| `ls -la && echo "---" && git log --oneline -5 2>/dev/null` | `git log` |
| `cd harness && cat persona.py && echo "PERSONA_CONFIG" && cat persona_config.py` | `cat persona.py  (+1 more)` |
| `cd harness && ls templates && echo "---" && find templates -type f \| head && echo "CONFIG" && cat config.py \| head -80` | `find templates  (+1 more)` |
| `cd harness && grep -rn "system_prompt\|system prompt\|..." *.py \| head -40` | `grep "system_prompt..."` |
| `cd harness && nl -ba tracing_agent.py \| head -90` | `nl -ba tracing_agent.py` |
| `cd harness && python3 -c "import yaml,sys; ..."` | `python3 -c` |
| `cd harness` (all noise) | `cd harness` *(fallback: capped full label)* |
| `` (empty) | `` *(fallback)* |

(`find templates -type f` → `find templates`: `-type f` is a flag, `templates`
is the first non-flag token. `+1 more` counts the trailing `cat config.py`; the
`ls templates` and two `echo`s are noise and not counted.)

## Testing (TDD)

Table-driven unit tests for `summarize_command` covering every row above plus:
- quoted-pattern truncation,
- redirect/pipe stripping,
- all-noise → fallback,
- empty/whitespace → fallback,
- a single real command with no chain → no `(+N more)`.

Plus a `lines_for` test asserting the rendered markup uses the summary and the
`$muted` token for the count. Existing `task_tree` / `activity_region` tests must
stay green.

## Files

- `harness/tui/widgets/task_tree.py` — add `summarize_command`, apply in
  `lines_for`.
- `tests/test_tui_widgets.py` — add the table tests.

No new files, no state changes, no dependency changes.
