# DoneDone TUI — component catalog

The reusable visual components for the `dn` TUI. This is the **approved design
system**: when building or changing TUI UI, base it on these components and the
shared tokens — don't invent one-off widgets or hardcode colors.

- **Decisions & rationale:** `docs/superpowers/specs/2026-06-26-tui-design-system-design.md`
- **Living brand book (see it rendered):** `harness/tui/styles/brandbook.html` —
  the palette, glyph map, status states, and shipped components rendered on the
  real terminal background. Generated from the live tokens; refresh with
  `python -m harness.tui.styles.brandbook` after any token change.
- **Tokens (source of truth):** `harness/tui/theme.py` (`HARNESS_THEME.variables`,
  `COLORS`, `STATUS_COLOR`)
- **State the components read:** `harness/tui/state.py` (`FleetSnapshot` /
  `AgentSnapshot`). `AgentSnapshot` now carries `tools: tuple[ToolView, ...]` (all
  of a turn's tools, by id) alongside `tool` (the live single tool).

## For agents — read this first

**This file is the canonical source for "what component/token do I use, and may I
add one?"** (Not `brandbook.html` — that is the *human*, rendered view; this text
is the machine-readable one.) The rule:

> **Reuse before you invent.** First find the component below; extend it or
> compose existing ones; only add a **new** entry (with rationale in the spec) if
> nothing fits. Never hardcode a color or glyph — use the tokens
> (`theme.py` / `tokens.py`).

**Status tags — do not "reuse" something that isn't shipped.** Every entry below
is tagged; only `✅` components actually exist and run today:

- **`✅ shipped`** — a wired widget class you can use now.
- **`🟡 built · unwired`** — the class exists but nothing mounts it. Wire it before
  relying on it; don't assume it renders.
- **`📐 designed-only`** — spec/catalog entry with **no implementation**. Build it
  (per spec) before use; treat as a plan, not an API.
- **`◻ inlined`** — a real surface, but drawn directly in `app.py` (no standalone
  widget). Change it there, not in `widgets/`.

When in doubt, the ground truth is the code: a component is `✅` only if its class
is imported/mounted in `harness/tui/app.py` (or mounted by a widget that is).

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
- **When to use:** for a *settled* status label (a state that just is). Use
  `ActivityStatus` instead when work is *live* (it carries elapsed/tokens and the
  looping glyph); a chip is static.

```
RUNNING   QUEUED   SCHEDULED   COMPLETED   FAILED
 blue     slate     amber       green       red
```

### `StateDot` / `ActivityGlyph`
Leading state indicator. `StateDot` is static (`• ◐ ▌ ✓ ✗ ⏱ ?`). `ActivityGlyph`
is the **single looping animation** in the whole UI (active `◐` cycle).
- **In:** `state`
- **Reduced-motion:** `ActivityGlyph` → static `◐`.
- **When to use:** `StateDot` to mark an agent's state in a *list* (the fleet
  rail), where many states show at once. `ActivityGlyph` only for the *one*
  actively-working agent — never run two looping glyphs at once (brand restraint).

### `StatusChip.for_yolo` — clickable footer mode line
A `StatusChip` mounted in the status bar that toggles a **session mode** on
click. First use: the permission bypass. The pattern generalizes to any binary
session mode (backend, fleet-mode, …).
- **In:** `(active: bool, pinned: bool)` → `StatusChip.for_yolo(...)`.
- **Look:** off = `▶▶ bypass permissions off` (muted); on = `▶▶ bypass
  permissions on` (**RED** `$error`, bold); pinned adds ` · pinned`. Glyph
  `▶▶` = `GLYPH["bypass"]`. **Plain-words posture, not jargon** — a user reads
  the security state directly. Red on the active state is the loudest signal:
  a full bypass that auto-runs commands. The safe state stays muted (quiet, not
  cryptic). Wording mirrors Claude Code's own permission-mode footer.
- **When to use:** for a binary *session mode* the user toggles and must always
  see (bypass, later backend/fleet). Use `PermissionModal` instead for a one-off
  per-command yes/no — `for_yolo` is a persistent posture, not a prompt.
- **Click → action:** the app's `on_click` (guarded on `#statusbar-mode`) calls
  `action_toggle_yolo()`, which flips the live state, refreshes the line in
  place (`_refresh_yolo_chip`), and fires `ext_method("harness/set_yolo",
  {active})`. Also toggled by `/yolo` (no shift+tab — terminal-finicky here).
- **Persisting is a SEPARATE gesture.** A click only flips the *live* mode
  (loud, reversible). Making a mode *survive launches* is the deliberate
  `/yolo pin` (writes `yolo_pinned` to `done.conf`) — never the click. This
  split is the pattern's safety contract; reuse it for any persisted mode.
- **Placement = far LEFT of the status bar** (mounted first), where the eye
  lands — a security-sensitive mode must not be buried behind the `1fr` cwd at
  the right edge (where it clips on narrow terminals). The `#statusbar` is a
  `layout: horizontal` row (chip · cwd · version). Also mirror a compact marker
  into the top mode line (`Build · bypass on · model`, red) so it shows top
  **and** bottom.

```
▶▶ bypass permissions off      ▶▶ bypass permissions on      ▶▶ bypass permissions on · pinned
 muted                          red                           red
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
- **When to use:** for the agent's *prose* answer. Tool calls and reasoning are
  NOT this — they go to `ActivityRegion` (principle #7); never push tool output
  into the transcript stream.

### `UserMessage`  *(exists today — promoted)*
The accent-bar user line (`▌ bold text`, `.user-msg`).
- **When to use:** for what the *human* sent, to anchor each turn. The `▌` accent
  bar is the user marker — don't reuse it for agent output (that's borderless).

---

## C. Work-in-progress

### `ActivityStatus`  ⭐
The live activity line: `· <label>… (1m 18s · ↓ 4.0k tokens)`.
- **In:** `(activity_label, elapsed, tokens, state)`
- **Drives:** the one looping `ActivityGlyph` + a `set_interval` elapsed tick.
- **Supersedes:** today's bare `LoadingIndicator` (`#working`).
- **When to use:** as the single live-work line while the agent is busy. Use a
  `StatusChip` instead once work has *settled* — `ActivityStatus` blanks itself
  when state is idle/done/failed and is not a record.

```
· Asking clarifying questions…  (1m 18s · ↓ 4.0k tokens)
```

### `TaskTree`  ⭐
Nested checklist, updated in place.
- **In:** `tasks: tuple[TaskItem, ...]`
- Glyphs: `✓` done · `▣` in-progress · `□` pending.
- **When to use:** for a *multi-step plan* with known sub-steps. NOT for raw tool
  commands — the status-only decision retired that (the per-command summary was a
  whack-a-mole liability); `TaskTree` is currently unwired pending real plan data.

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
- **When to use:** as *the* home for all in-flight tool/work activity — pinned and
  transient. Anything about "what the agent is doing now" belongs here, never in
  the transcript (principle #7). Settled records are a separate concern.

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
- **When to use:** for one tool call's detail *inside* `ActivityRegion` (ctrl+o).
  Don't mount it directly in the transcript or app — it's a child of the region,
  not a standalone surface.

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
- **When to use:** when the agent wants *richer input than yes/no* (pick an option,
  refine a plan) and isn't hard-blocked — it's inline & non-blocking. Use
  `PermissionModal` instead for a blocking allow/reject before a command runs.

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
- **When to use:** for a *blocking* per-command yes/no that must be answered before
  the agent proceeds. For a persistent "always allow" posture use
  `StatusChip.for_yolo`; for non-blocking richer input use `DecisionPrompt`.

### `SelectModal`  *(exists today — kept)*
Search + scrollable list modal; the base both modals extend.
- **When to use:** to pick *one item from a list* (model, persona, command). It's
  the base both modals extend — reuse it before building any new picker; don't
  hand-roll a list overlay.

### `SlashMenu`  *(exists today — input/nav)*
Filtered command list, mounted/removed by the app as `/` is typed/cleared.
- **In:** `list[Command]`; `update_query`, `move`, `highlighted_command`.
- **When to use:** for *command discovery while composing* (the `/` menu). It's a
  transient composer affordance — use `SelectModal` instead for a full-screen
  pick that isn't tied to typing in the prompt.

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

## Catalog at a glance — with real status

Verified against `harness/tui/app.py` + `harness/tui/widgets/`. Tags:
`✅ shipped` · `🟡 built·unwired` · `📐 designed-only` · `◻ inlined in app.py`.

| Group | Component | Status | Where |
|---|---|---|---|
| **A** primitives | `StatusChip` (+ `for_yolo`) | ✅ shipped | `widgets/status_chip.py`, used in `app.py` |
| | `StateDot` | 🟡 built·unwired | class exists, not mounted |
| | `ActivityGlyph` | 🟡 built·unwired | class exists, not mounted |
| | `Hairline` / `SectionLabel` | 📐 designed-only | no class |
| **B** responses | `AnswerStream` (streaming Markdown) | ◻ inlined | drawn in `app.py` (`_stream_message`) |
| | `UserMessage` (`▌` accent line) | ◻ inlined | drawn in `app.py` |
| **C** work | `ActivityRegion` | ✅ shipped | wired in `app.py` |
| | `ActivityStatus` | ✅ shipped | mounted by `ActivityRegion` |
| | `ToolCallRow` | ✅ shipped | mounted by `ActivityRegion` (ctrl+o) |
| | `TaskTree` | 🟡 built·unwired | `display=False` always (status-only decision) |
| | `ProgressRow` | 📐 designed-only | no class |
| **D** decisions | `PermissionModal` | ✅ shipped | wired in `app.py` |
| | `SelectModal` | ✅ shipped | wired in `app.py` |
| | `DecisionPrompt` | 🟡 built·unwired | class exists; reducer fills `decision`, but no mount |
| **—** input/nav | `SlashMenu` | ✅ shipped | wired in `app.py` |
| | `PromptArea` | ✅ shipped | wired in `app.py` |
| | `StatusBar` / footer meta | ◻ inlined | drawn in `app.py` |
| **E** future | `ScheduleBadge` · `CronRow` | 📐 designed-only | no class; `schedule` snapshot field unpopulated |
| **F** shell/nav | `AppShell` · `AgentRail` · `SidebarToggle` · `FleetHeader` | 📐 designed-only | no class (fleet phase) |

**Reality check:** only the `✅` rows are usable today. `SlashMenu` / `PromptArea`
ship but were missing from the original A–F grouping — listed here under
input/nav. The `*`/`⭐` markers used in older revisions of this file meant
"planned for the on-ramp," not "exists" — they were aspirational and have been
replaced by the status column above.

When a new UI need arises: **first find the component here; extend it or compose
existing ones; only add a new entry to this catalog (with rationale in the spec) if
nothing fits.**
