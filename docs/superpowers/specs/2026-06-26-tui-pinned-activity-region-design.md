# TUI pinned activity region — tool calls out of the transcript

**Status:** design / spec (no implementation in this doc)
**Date:** 2026-06-26
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Scope:** the DoneDone (`dn`) Textual TUI — move tool-call activity out of the
conversation scroll into a pinned, transient region; fix transcript spacing and
three rendering bugs.
**Builds on:** the design system shipped in PR #30
(`docs/superpowers/specs/2026-06-26-tui-design-system-design.md`,
`harness/tui/styles/components.md`, `harness/tui/state.py`).
**References studied:** OpenCode Go TUI (`message.go`/`messages.go`) and Claude
Code interactive mode — see the research note in session memory
(`tui-tool-call-rendering-research`).

---

## 1. Problem

In the current TUI, tool calls render **inline in the transcript scroll as if they
were content** (peer widgets mounted alongside the streaming Markdown answer). This
produces three problems, visible in real runs:

1. **Tool calls look like responses.** A `$ cmd` row with a status chip sits in the
   same column and flow as the agent's prose, so the eye can't tell "the agent is
   talking to me" from "the agent is doing plumbing."
2. **Ordering disconnect.** The answer appears, then tool rows appear above/below it.
   Worse, `tool_update` reads `self._snapshot.active.tool` — which is only ever the
   *latest* tool — so an update for an earlier tool row renders the wrong tool's
   status. The reducer tracks one `tool`, not per-id tools.
3. **No spacing.** User message, answer, and tool rows stack flush with no
   whitespace, so the blocks blur together.

Plus three smaller rendering bugs (see §5).

## 2. Decision: a pinned, transient activity region

Tool-call activity moves **out of the transcript** into **one fixed spot docked just
above the composer**. The transcript becomes a clean conversation of two block types
only — **user messages** and **agent responses** — with breathing room between them.

Layout (bottom of screen):

```
  … transcript (messages + responses, blank-line spaced) …
──────────────────────────────────────────────   ← rule
◐ Running pytest…  (4s)            ctrl+o details  ← ActivityRegion (pinned, transient)
  ✓ Read app.py   ▣ Bash pytest                    ← collapsed checklist / expanded detail
┌ Reply…                                         ┐ ← composer
```

Settled decisions (from brainstorming):

- **D1 — Pinned, not inline.** Tool calls never append to the transcript. The
  `ActivityStatus` + `TaskTree` widgets (already built in PR #30, already mounted
  above the status bar) become this region. This deletes the wrong-tool inline bug by
  construction.
- **D2 — Clears to idle after a turn.** On turn end the region empties (zero height /
  blank). The transcript keeps only user + answer blocks. Tool history is NOT
  preserved in the scroll — that is what the trace / `events.jsonl` is for.
- **D3 — Expandable on demand.** Compact status while running (gerund + elapsed +
  task-tree checklist); `ctrl+o` expands to per-tool detail (command + tailored,
  capped output), then collapses back.
- **D4 — Transcript spacing.** One blank line between a user message and its answer,
  and between turns. The user block keeps its accent bar; the answer sits below with
  a gap. Matches the brand's negative-space grammar.
- **D5 — Reducer tracks all tools by id.** `AgentSnapshot` gains
  `tools: tuple[ToolView, ...]` (ordered, one per `tool_call_id`) alongside the
  existing `tool` (the current/live tool, kept for the status line). `tool_update`
  updates the matching tool **by id** — the structural fix for the wrong-tool bug,
  done in the pure reducer where it is testable.

## 3. Components

| Component | Status | Role |
|---|---|---|
| `ActivityRegion` (new, `widgets/activity_region.py`) | NEW | The pinned, transient zone: a top rule + `ActivityStatus` line + collapsed `TaskTree` checklist + (on toggle) per-tool `ToolCallRow` detail. Renders nothing (zero height) when idle. `update_from(snapshot)`, `toggle_details()`. |
| `ActivityStatus` | kept + fixed | Live status line (gerund + elapsed). Owns the single ellipsis (§5); token clause hidden when 0 (§5). |
| `TaskTree` | kept | The collapsed per-turn tool checklist (`✓`/`▣`/`□`/`✗`). Now lives inside `ActivityRegion`. |
| `ToolCallRow` | repurposed | No longer a transcript widget. Becomes the **expanded-detail row inside `ActivityRegion`**: subtype glyph + formatted title + status chip + a tailored, capped body. Reads a `ToolView`. |
| `AnswerStream` / `UserMessage` | kept | The only transcript block types now. |

**Body tailoring in the expanded view (`ToolCallRow`)**, per the OpenCode reference:
read → first ~6 lines; edit → unified diff; bash → `$ cmd` + stdout; generic → first
~10 lines; no-op tools (e.g. a `todoread`-equivalent) hidden. Display-only; capping
lives in the view layer.

## 4. Reducer change (`state.py`)

- Add `tools: tuple[ToolView, ...] = ()` to `AgentSnapshot`.
- On a `tool` item: append a `ToolView` (title, status, subtype) to `tools`, and set
  `tool` = that view (current/live). Append the `TaskItem` to `tasks` (unchanged).
- On a `tool_update` item: update the `ToolView` in `tools` **whose id matches**
  `item.id`, and update the matching `TaskItem`. `tool` (live) points at the matching
  view. (`ToolView` gains an `id: str` field for this — match by id, never by array
  position. If no id matches, no-op gracefully.)
- On `TurnStarted`: reset `tools=()` (as `tasks`/`tool` already reset).
- On `TurnEnded`: `tool=None`, `tools` may stay (region clears via state≠working).

`ToolView` gains an `id: str` field so updates match by id, not array position
(this is the precise fix for the bug where the last tool was always updated).

## 5. Folded-in polish fixes

1. **Inline-code grey blocks.** `app.tcss` `#transcript Markdown .code_inline` sets
   only `color: $code`; Textual's default code background bleeds through as grey. Add
   a `background:` reset (transparent / `$background`), keep the brand-blue tint.
2. **`Responding……` double ellipsis.** Reducer labels currently end in `…`
   (`"Thinking…"`, `"Responding…"`) AND `ActivityStatus.line_for` appends `…`. Fix:
   reducer labels drop the ellipsis (`"Thinking"`, `"Responding"`, `"Running
   <subtype>"`); the widget owns exactly one `…` while working. One source of truth.
3. **`↓ 0 tokens` mid-stream.** Hide the token clause entirely when the count is 0
   (`◐ Responding… (38s)` with no token text) until a real count arrives. (Why usage
   isn't reported by some providers is a separate plumbing question, out of scope.)

## 6. app.py integration

- `on_session_update`: `message` → stream; `thought` → muted line; `user` → accent
  block; `tool` / `tool_update` → **`_apply(...)` + refresh `ActivityRegion` only**
  (no `_append`, no inline `ToolCallRow`). Remove `self._tool_rows`.
- `_enter_conversation`: mount a single `ActivityRegion` docked above the composer
  (replacing the separate `ActivityStatus` + `TaskTree` mounts).
- `_reset_conversation`: reset `_snapshot` (already does) — drop `_tool_rows` reset
  (gone).
- New `ctrl+o` binding → `ActivityRegion.toggle_details()`.
- Preserve: the `gen`/`session_id` guards, the streaming-Markdown answer path
  (`_stream_message`), the `stream_reset` boundary handling, permission/cancel/clear
  plumbing.

**Transcript spacing (D4):** `app.tcss` — add vertical margin so the user block and
the answer have one blank line between them, and turns are separated. Keep the
existing `.user-msg` accent bar.

## 7. Testing

- **Reducer (`test_tui_state.py`):** two tools in one turn → `tools` has both; a
  `tool_update` for the FIRST updates the first (regression test for the wrong-tool
  bug); `TurnStarted` resets `tools`; `TurnEnded` → `tool=None`.
- **Widgets (`test_tui_widgets.py`):** `ActivityStatus` line has exactly one `…`;
  token clause absent when tokens==0, present when >0; `ActivityRegion` renders blank
  when idle, a collapsed checklist while working, and per-tool detail after
  `toggle_details()`.
- **Pilot (`test_tui_pilot.py`):** after a tool call there is **NO `ToolCallRow` in
  `#transcript`** (inverse of the PR-30 test — tools are not inline); the pinned
  `ActivityRegion` shows the tool; `ctrl+o` expands it; the transcript holds only
  user/answer blocks. Update/replace the PR-30 test that asserted an inline
  `ToolCallRow`.
- **CSS:** inline-code background reset; transcript block spacing.

## 8. Scope

One coherent change: pinned `ActivityRegion` + reducer per-tool fix + 3 polish fixes
+ transcript spacing. No fleet work. New worktree → PR (done: worktree
`worktree-tui-pinned-activity`).

**Out of scope:** preserving tool history in the scroll (trace/events covers it); the
provider-usage plumbing behind `0 tokens`; per-tool inline blocks (rejected in favor
of the pinned region).

## 9. Summary

- Tool calls leave the transcript for a **pinned, transient activity region** above
  the composer; the scroll is just **messages + responses**, blank-line spaced.
- The region is **compact while running, `ctrl+o`-expandable** to per-tool detail,
  and **clears to idle** after a turn.
- The reducer tracks **all of a turn's tools by id**, fixing the wrong-tool bug in
  the pure layer.
- Three polish fixes folded in: inline-code background, double ellipsis, `0 tokens`.
- `components.md` updated: `ActivityRegion` new; `ToolCallRow` re-scoped to the
  region; documented rule "transcript = messages + responses; tool activity = pinned
  + transient."
