# DoneDone TUI — component catalog

The reusable visual components for the `dn` TUI. This is the **approved design
system**: when building or changing TUI UI, base it on these components and the
shared tokens — don't invent one-off widgets or hardcode colors.

- **Decisions & rationale:** `docs/superpowers/specs/2026-06-26-tui-design-system-design.md`
- **Tokens (source of truth):** `harness/tui/theme.py` (`HARNESS_THEME.variables`,
  `COLORS`, `STATUS_COLOR`)
- **State the components read:** `harness/tui/state.py` (`FleetSnapshot` /
  `AgentSnapshot`)

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

### `ToolCallRow`
One tool call: subtype glyph + title + `StatusChip` + one-line output,
expand/collapse.
- **In:** `ToolView` (title, status, subtype, body)
- Subtype glyph is **inferred for display only** (neutral `$` fallback).

```
✎ harness/api.ts                       RUNNING
  → in_progress   applying patch (3 hunks)
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
A primitives   StatusChip · StateDot/ActivityGlyph · Hairline/SectionLabel
B responses    AnswerStream* · UserMessage*
C work         ActivityStatus⭐ · TaskTree⭐ · ToolCallRow · ProgressRow
D decisions    DecisionPrompt⭐ · PermissionModal* · SelectModal*
E future       ScheduleBadge · CronRow
F shell/nav    AppShell · AgentRail · SidebarToggle · FleetHeader · StatusBar*

   * = exists today, kept/promoted     ⭐ = headline new, ships in the on-ramp
```

When a new UI need arises: **first find the component here; extend it or compose
existing ones; only add a new entry to this catalog (with rationale in the spec) if
nothing fits.**
