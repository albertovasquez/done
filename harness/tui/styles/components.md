# DoneDone TUI — component catalog

The reusable visual components for the `dn` TUI. This is the **approved design
system**: when building or changing TUI UI, base it on these components and the
shared tokens — don't invent one-off widgets or hardcode colors.

- **Decisions & rationale:** `docs/superpowers/specs/2026-06-26-tui-design-system-design.md`
- **Tokens (source of truth):** `harness/tui/theme.py` (`HARNESS_THEME.variables`,
  `COLORS`, `STATUS_COLOR`)
- **State the components read:** `harness/tui/state.py` (`FleetSnapshot` /
  `AgentSnapshot`). `AgentSnapshot` now carries `tools: tuple[ToolView, ...]` (all
  of a turn's tools, by id) alongside `tool` (the live single tool).

## Principles (apply to every component)

1. **Dumb & reactive.** A component reads a slice of a snapshot and renders it. It
   never computes state transitions — that is the reducer's job (`state.reduce`).
2. **One purpose.** If a widget is doing two jobs, split it.
3. **Tokens only.** No hardcoded hex outside `theme.py` / `COLORS`. Use semantic
   tokens (`$accent`, `$muted`, status tokens, glyph map).
4. **Brand voice = restraint** (Brand Book p.10). Motion communicates a *state
   change*, never decoration. Exactly one looping animation on screen (the active
   glyph); transitions ≤250ms ease-out; never animate during heavy streaming. Every
   effect has a reduced-motion + monochrome fallback.
5. **Fleet-shaped, N=1 correct.** Components work with one agent today and scale to
   the fleet unchanged.
6. **Testable.** Each component gets a pilot and/or snapshot test.
7. **Transcript = user messages + agent responses only.** Tool-call activity is
   pinned + transient (in `ActivityRegion`), never inline. This separation keeps the
   conversation thread clean and allows tool details to be toggled independently.

## Tokens

### Color

```
Brand core        accent #286CE9 · fg #E3E3E3 · slate/muted #8690A3
                  bg #0A1524 · surface #16243A · error #E02F07
Product status    done/success #7ee787 · scheduled/attention #e3b341
  (sanctioned brand extension for product UI — see spec §4.1)
Derived           muted-deep #5B6577 · code #9DB8E8 · accent-30 (#286CE9 @30%)
```

### Glyphs

```
state dots    idle •   active ◐(anim)   responding ▌
terminal      done ✓   failed ✗
future/decide scheduled ⏱   awaiting ? / ▌pulse
brand         ≡ mark
tool subtype  edit ✎   test ⚑   read ◇   shell $   search ⌕
```

Status is always carried by **color + glyph + weight together**, so meaning
survives monochrome terminals.

---

## A. Primitives

### `StatusChip`
Uppercase status pill: `RUNNING` / `QUEUED` / `SCHEDULED` / `COMPLETED` / `FAILED`.
The atomic status atom, reused everywhere.
- **In:** `(state, ToolStatus | None)`
- **Look:** tracked bold caps, status-token color.

```
RUNNING   QUEUED   SCHEDULED   COMPLETED   FAILED
 blue     slate     amber       green       red
```

### `StateDot` / `ActivityGlyph`
Leading state indicator. `StateDot` is static (`• ◐ ▌ ✓ ✗ ⏱ ?`). `ActivityGlyph`
is the **single looping animation** in the whole UI (active `◐` cycle).
- **In:** `state`
- **Reduced-motion:** `ActivityGlyph` → static `◐`.

### `StatusChip.for_yolo` — clickable footer mode chip
A `StatusChip` mounted in the status bar that toggles a **session mode** on
click. First use: YOLO (permission auto-allow). The pattern generalizes to any
binary session mode (backend, fleet-mode, …).
- **In:** `(active: bool, pinned: bool)` → `StatusChip.for_yolo(...)`.
- **Look:** off = `• ask` (muted); on = `! YOLO` (amber `$scheduled`, bold);
  pinned adds ` · pin`. Glyph `!` = `GLYPH["bypass"]`. Amber signals a
  security-sensitive on-state without a per-command banner (restraint, p.4).
- **Click → action:** the app's `on_click` (guarded on `#statusbar-mode`) calls
  `action_toggle_yolo()`, which flips the live state, refreshes the chip in
  place (`_refresh_yolo_chip`), and fires `ext_method("harness/set_yolo",
  {active})`.
- **Persisting is a SEPARATE gesture.** A click only flips the *live* mode
  (loud, reversible). Making a mode *survive launches* is the deliberate
  `/yolo pin` (writes `yolo_pinned` to `done.conf`) — never the click. This
  split is the pattern's safety contract; reuse it for any persisted mode.
- **Placement = far LEFT of the status bar** (mounted first), where the eye
  lands — a security-sensitive mode must not be buried behind the `1fr` cwd at
  the right edge (where it clips on narrow terminals). For an always-on bypass,
  also mirror the marker into the top mode line (`Build · YOLO · model`,
  amber) so it shows top **and** bottom.

```
· ask          ! YOLO          ! YOLO · pin
 muted          amber           amber
```

### `Hairline` / `SectionLabel`
Brand grammar primitives: a thin rule, and a tracked-bold-caps label
(`AGENTS`, `CURRENT TASKS`).

---

## B. Responses

### `AnswerStream`  *(exists today — kept unchanged)*
The canonical response renderer: the live `Markdown` widget that accumulates deltas
and `.update()`s per token (`app._stream_message`). The reducer marks the agent
`responding`; this widget owns the text. **Do not replace.**

### `UserMessage`  *(exists today — promoted)*
The accent-bar user line (`▌ bold text`, `.user-msg`).

---

## C. Work-in-progress

### `ActivityStatus`  ⭐
The live activity line: `· <label>… (1m 18s · ↓ 4.0k tokens)`.
- **In:** `(activity_label, elapsed, tokens, state)`
- **Drives:** the one looping `ActivityGlyph` + a `set_interval` elapsed tick.
- **Supersedes:** today's bare `LoadingIndicator` (`#working`).

```
· Asking clarifying questions…  (1m 18s · ↓ 4.0k tokens)
```

### `TaskTree`  ⭐
Nested checklist, updated in place.
- **In:** `tasks: tuple[TaskItem, ...]`
- Glyphs: `✓` done · `▣` in-progress · `□` pending.

```
└ ✓ Explore project context
  ▣ Ask clarifying questions
  □ Propose approaches
  □ Present design sections
```

### `ActivityRegion`  ⭐
The pinned, transient zone above the composer. Compact while working
(`ActivityStatus` line + `TaskTree` checklist); `ctrl+o` expands to per-tool detail
(`ToolCallRow` rows); renders empty when idle or the turn ends. **Owns**
`ActivityStatus` + `TaskTree` + all `ToolCallRow` instances. This is where tool-call
activity lives — never in the transcript (principle #7).
- **In:** `AgentSnapshot` (reads `state`, `activity_label`, `elapsed`, `tokens`,
  `tasks`, `tools`)
- **Methods:** `update_from(snapshot)`, `toggle_details()`
- **State:** `is_idle(snap)` → render nothing (zero height); details toggled → show
  each `tools` entry as an expanded `ToolCallRow`.

```
──────────────────────────────────────────
◐ Running test…  (4s)                ctrl+o details
└ ✓ Read app.py   ▣ Bash pytest
  [when expanded — per-tool detail rows]
  ✎ harness/api.ts                       RUNNING
    → in_progress   applying patch (3 hunks)
```

### `ToolCallRow`
One tool call, rendered as a **collapsed one-liner or expanded detail row inside
`ActivityRegion`** — not a transcript widget.
- **In:** `ToolView` (id, title, status, subtype, body)
- **Methods:**
  - `line_for(tool)` → collapsed: subtype glyph + title + `StatusChip`.
  - `detail_for(tool)` → expanded: header + capped body.
  - `cap_body(body, subtype)` → per-subtype line cap (`read`=6, generic=10).
- Subtype glyph is **inferred for display only** (neutral `$` fallback).

```
[collapsed]   ✎ harness/api.ts                       RUNNING
[expanded]    ✎ harness/api.ts                       RUNNING
              applying patch (3 hunks)…
```

### `ProgressRow`
Columnar task row from the mockups: `TASK · STATUS · PROGRESS · ELAPSED`.
- **In:** a task with optional `progress` (0–100).
- `ProgressBar` when total known; `ActivityGlyph` when unknown.

```
● Index repo dependencies     RUNNING   64% ▓▓▓▓▓▓░░░   00:18:42
```

---

## D. Decisions needed

### `DecisionPrompt`  ⭐
The "grill-me" clarification UI. **One model, two render targets:** inline in the
transcript for refinement questions; escalates to a **modal** only when the agent is
truly blocked.
- **In:** `DecisionView` (question, options[title + dimmed rationale], fallbacks)
- Keyboard: number / ↑↓ / enter. Fallbacks: `Type something`, `Chat about this`.
- The answer persists in the transcript as a record.

```
Where should the streaming seam live?  …

› 1. Our own streaming model wrapper
     A LitellmModel subclass that overrides query… Recommended.
  2. Thread callback through TracingAgent.query()
     Mixes agent-loop concerns with model-call concerns…
  3. Patch upstream litellm_model.py
     Simplest diff, but edits vendored code…
  4. Type something
  5. Chat about this
```

### `PermissionModal`  *(exists today — kept)*
Command-permission modal. Sibling of `DecisionPrompt` ("agent needs your input");
shares footer / keybinding styling.

### `SelectModal`  *(exists today — kept)*
Search + scrollable list modal; the base both modals extend.

---

## E. Future / scheduled

### `ScheduleBadge`
The "something will happen later" signal.
- **In:** `ScheduleView`
- Amber `⏱`.

```
SCHEDULED · in 2d 14h          cron · every 24h · next 04:00
```

### `CronRow`
A scheduled job as a row (for a crons sidebar). Sibling of `ProgressRow` for future
work.
- **In:** `ScheduleView`

---

## F. Shell & navigation

### `AppShell`
Responsive frame: `[left rail] [main column] [right rail] [status bar]`, two
collapsible sidebars.
- **In:** `FleetSnapshot` + UI prefs.
- **N=1 / narrow:** collapses to today's single-column LANDING / CONVERSATION.

### `AgentRail`
The AGENTS list: per-agent name + `StateDot` + `StatusChip` + sub-line
(`editing api.ts` / `idle · 1 task`), selectable.
- **In:** `FleetSnapshot.agents`
- **N=1:** hidden / collapsed.
- **Recommended placement:** right rail (per mockups; adjustable per phase).

### `SidebarToggle`
Open/close affordances for the left & right rails (`≡`-style glyph + keybinding).
- **In:** toggle state.

### `FleetHeader`
Top bar: wordmark / `≡`, active-agent name + state, `3 online · 2 running` counts,
model label.
- **In:** `FleetSnapshot` + model. Extends today's header / status bar.

### `StatusBar`  *(exists today — kept)*
Bottom hairline bar; gains keybinding-hint segments
(`tab switch · / prompt · ctrl+p commands · q quit`).

---

## Catalog at a glance

```
A primitives   StatusChip (+ for_yolo footer mode chip) · StateDot/ActivityGlyph · Hairline/SectionLabel
B responses    AnswerStream* · UserMessage*
C work         ActivityRegion⭐ (owns ActivityStatus⭐ · TaskTree⭐ · ToolCallRow) · ProgressRow
D decisions    DecisionPrompt⭐ · PermissionModal* · SelectModal*
E future       ScheduleBadge · CronRow
F shell/nav    AppShell · AgentRail · SidebarToggle · FleetHeader · StatusBar*

   * = exists today, kept/promoted     ⭐ = headline new, ships in the on-ramp
```

When a new UI need arises: **first find the component here; extend it or compose
existing ones; only add a new entry to this catalog (with rationale in the spec) if
nothing fits.**
