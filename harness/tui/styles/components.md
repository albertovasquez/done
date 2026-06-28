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

### `TaskTree`  `🟡 built·unwired`
Live checklist, updated in place — a plan's steps, struck through as they finish.
- **In:** `tasks: tuple[TaskItem, ...]`
- **Glyphs:** `✓` done · `▣` in-progress · `□` pending · `✗` failed.
- **Desired look (target, from the concept screenshot):** sits *under* an
  `ActivityStatus` line and **strikes through completed items** so the eye tracks
  what's left, not what's done. The activity line carries live elapsed/tokens;
  the tree carries the plan. (Today's `lines_for` has no strikethrough and the
  widget is never displayed — this is the look to build toward when it's revived.)
- **When to use:** for a *multi-step plan* with known sub-steps. NOT for raw tool
  commands — the status-only decision retired that (the per-command summary was a
  whack-a-mole liability), which is why `TaskTree` is currently unwired; revive it
  only with real plan/subtask data, in the struck-through form above.

```
◦ Stewing…  (4m 45s · ↓ 17.0k tokens)
  └ ✓ S̶a̶v̶e̶ ̶c̶o̶n̶c̶e̶p̶t̶ ̶m̶o̶c̶k̶u̶p̶s̶          (done → struck through, muted)
    ✓ A̶d̶d̶ ̶c̶o̶m̶p̶o̶n̶e̶n̶t̶s̶ ̶t̶o̶ ̶c̶a̶t̶a̶l̶o̶g̶
    ▣ Render mocks in brand book          (in-progress → bright)
    □ Wire it up                          (pending → muted)
```

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

### `ToolCallRow`   `✅ shipped`
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

**Desired look — edit summary (target, from a concept screenshot):** an *edit*
tool should summarize its change as a one-line **`+N / −N` diff-stat** with a
state-colored leading dot, instead of just a status word:

```
● Update(docs/.../persona-C2b-rail-design.md)
  └ Added 42 lines, removed 11 lines
```

- **Not shipped:** `line_for` today renders `glyph + title + STATUS` only, and
  `ToolView` carries **no line-change counts** — nothing in the TUI computes
  `+N/−N`. Building this needs an **upstream data change**: `ToolView` gains
  `added: int | None` / `removed: int | None`, the engine/`render.py` computes them
  for edit/write tools, then `ToolCallRow` renders the summary.
- This is the *settled-edit* seed of **`ToolResultBlock`** (the OpenCode spike's
  collapsed transcript record). Color rule: dot = status token (green done / blue
  running / red failed); `+N` in `$success`, `−N` in `$error` when shown.

### `ProgressRow`
Columnar task row from the mockups: `TASK · STATUS · PROGRESS · ELAPSED`.
- **In:** a task with optional `progress` (0–100).
- `ProgressBar` when total known; `ActivityGlyph` when unknown.

```
● Index repo dependencies     RUNNING   64% ▓▓▓▓▓▓░░░   00:18:42
```

---

## D. Decisions needed

### `DecisionModal`  ⭐
The "grill-me" clarification UI, rendered as a centered modal overlay (shares
`SelectModal`'s box styling). Dims the conversation and owns focus, so it reads as
"I'm blocked, pick one" rather than a dim inline block.
- **In:** `DecisionView` (question, options[title + dimmed rationale], fallbacks)
- Keyboard: number / ↑↓ / enter; esc cancels. Fallbacks: `Type something`, `Chat about this`.
- The first option is marked `(recommended)` (router emits options best-first; an
  explicit flag is GH #117).
- Dismisses with the chosen option index (or `TYPE_SOMETHING` / `CHAT_ABOUT_IT` /
  `None`); the app maps that to submit-title / focus / prefill / close.
- **When to use:** when the agent wants *richer input than yes/no* (pick an option,
  refine a plan). For a per-command allow/reject before a command runs use
  `PermissionModal` instead.

```
╭─ Where should the streaming seam live? ───── esc ─╮
│ › 1. Our own streaming model wrapper  (recommended)│
│      A LitellmModel subclass that overrides query… │
│   2. Thread callback through TracingAgent.query()  │
│      Mixes agent-loop concerns with model-call…    │
│   3. Patch upstream litellm_model.py               │
│      Simplest diff, but edits vendored code…       │
│   4. Type something                                │
│   5. Chat about this                               │
│ ↑↓ move · enter select · esc cancel                │
╰────────────────────────────────────────────────────╯
```

### `PermissionModal`  *(exists today — kept)*
Command-permission modal. Sibling of `DecisionModal` ("agent needs your input");
shares footer / keybinding styling.
- **When to use:** for a *blocking* per-command yes/no that must be answered before
  the agent proceeds. For a persistent "always allow" posture use
  `StatusChip.for_yolo`; for richer option-picking use `DecisionModal`.

### `SelectModal`  *(exists today — kept)*
Search + scrollable list modal; the base both modals extend.
- **When to use:** to pick *one item from a list* (model, persona, command). It's
  the base both modals extend — reuse it before building any new picker; don't
  hand-roll a list overlay.

### `NewPersonaModal`   `✅ shipped` (persona-create)
Name-a-new-persona overlay: an `Input` + a status line. Lifecycle: input → creating
(the `◐◓◑◒` spinner reused from `ActivityStatus`, reduced-motion static `◐`) →
`dismiss(id)` on success / inline `$error` on failure. Opened by `n` in the
`AgentRail`; on success the app creates the workspace (inert template trio) and
switches to it (the C2c `_apply_persona_switch` path). Sibling of
`SelectModal`/`PermissionModal`; the ONE create-input modal (no existing modal takes
a free-text *create* input with a create-then-switch lifecycle).
- **In:** none (collects a name); **Out:** the created id via `dismiss` (or None on esc).
- **When to use:** to *create* a persona. For PICKING an existing one use the rail;
  for a generic list pick use `SelectModal`.

### `SlashMenu`  *(exists today — input/nav)*
Filtered command list, mounted/removed by the app as `/` is typed/cleared.
- **In:** `list[Command]`; `update_query`, `move`, `highlighted_command`.
- **When to use:** for *command discovery while composing* (the `/` menu). It's a
  transient composer affordance — use `SelectModal` instead for a full-screen
  pick that isn't tied to typing in the prompt.

---

> **Visual reference for groups E & F:** the fleet/drawer/cron concept mockups
> (2026-06-27). Three screens — (1) full fleet dashboard with left `AgentRail` +
> `ProgressRow`s + a cron task row, (2) `AgentRail` as a right **drawer** over the
> chat with a header dropdown, (3) drawer closed. These encode the intended look
> the components below target. (Raw PNGs: drop into `docs/superpowers/assets/` if
> committing them; the decisions are captured in text here so they survive.)

## E. Future / scheduled

> **Reality:** there is **no cron/schedule backend** in the engine today, and
> `AgentSnapshot.schedule` / `ScheduleView` are defined but never populated. Both
> components below are `📐 designed-only` with **no data source** — build the
> backend (or wire a stub) before them.

### `ScheduleBadge`   `📐 designed-only`
The "something will happen later" signal — a single inline badge.
- **In:** `ScheduleView` (`label`, `when`)
- **Look:** amber `⏱` + relative time. From the mockup: `in 2d 14h SCHEDULED`,
  or a cron task row `□ Weekly report cron · emails reports … SCHEDULED`.
- **When to use:** to mark *one* future event inline next to its task/agent (e.g. a
  cron task in the task list, or a `scheduled · 1 job` rail sub-line). Use `CronRow`
  instead for a *dedicated list* of scheduled jobs.

```
□ Weekly report cron · emails reports          in 2d 14h   SCHEDULED
```

### `CronRow`   `📐 designed-only`
A scheduled job as a full row, for a crons list/sidebar. Sibling of `ProgressRow`.
- **In:** `ScheduleView`
- **Look (mockup):** `↻` cron glyph (amber) + name + cadence/next-run; in the rail,
  a `cron` status word on an agent that's mid-scheduled-run (`robbie · cron ·
  nightly-sync · syncing`).
- **When to use:** for a *roster* of scheduled jobs (a crons panel). For a single
  inline "happens later" hint, use `ScheduleBadge`.

---

## F. Shell & navigation

> **Reality:** `FleetSnapshot`/`AgentSnapshot` (with `active_id`) exist and are
> tested, the **persona indicator** (C2a) and the **`AgentRail`** (C2b widget,
> switching wired by C2c) ship today. The remaining shell widgets (`AppShell`,
> `SidebarToggle`, `FleetHeader`) are `📐 designed-only` — no classes yet. C2c
> wired in-process persona switching (`harness/set_persona`); the true
> N-concurrent fleet (live ticking / state dots) is a later pass. See
> `docs/superpowers/specs/2026-06-27-persona-C2c-design.md` and `…-C2-drawer-arc-design.md`.

### `PersonaIndicator` (status-bar chip)   `✅ shipped` (C2a, PR #46)
Shows **which agent/persona you're talking to**, sourced from the engine's real
resolved id (not the `--persona` flag) via `FleetSnapshot.active_id`.
- **In:** `FleetSnapshot.active_id` (set by `PersonaResolved` ← `persona_from_meta`)
- **Look:** a `#statusbar-persona` `Static` chip (`persona: fred`).
- **When to use:** as the always-visible "who am I addressing" anchor. This is the
  shipped seed of the whole fleet UI — the rail/drawer/header read the *same*
  `FleetSnapshot`. Don't echo the launch flag; read the engine-reported id.

### `AppShell`   `📐 designed-only`
Responsive frame: `[left rail] [main column] [right rail] [status bar]`, two
collapsible sidebars.
- **In:** `FleetSnapshot` + UI prefs.
- **N=1 / narrow:** collapses to today's single-column LANDING / CONVERSATION.
- **When to use:** as the *frame* once a rail/drawer exists. At N=1 it must be a
  no-op (collapse to today's view) — don't introduce it until C2b needs a rail.

### `AgentRail`   `✅ shipped` (C2b widget; C2c wires switching)
The AGENTS list. Per-agent card: `StateDot` (state-colored) + name + status word
(`active`/`running`/`cron`/`idle`/`scheduled`) + sub-line (`editing api.ts` /
`idle · 1 task` / `nightly-sync · syncing`). Selectable; active highlighted via
`FleetSnapshot.active_id`. Selecting a row switches the persona **in-process** via
the `harness/set_persona` ext-method (C2c) — no re-exec.
- **In:** the full roster from `list_personas()` (wired C2b via `roster.persona_rows`,
  active from `FleetSnapshot.active_id`). The live per-row `StateDot`/status word is
  still designed-only (no per-agent state source yet — deferred to a later fleet pass).
- **Two placements (mockups):** a persistent **left rail** (dashboard, image 1) or
  a toggled **right drawer** over the chat (`AGENTS  N · esc to close`, image 2).
  Footer: `↑↓ select · ⏎ switch · n new`.
- **When to use:** to *see and switch between* agents/personas. Use the drawer form
  when chat is primary (toggle on demand); the left rail when the fleet dashboard
  is primary. One `StateDot` per row — never one looping `ActivityGlyph` per row.

### `SidebarToggle`   `📐 designed-only`
Open/close affordance for the rail/drawer (`≡`-style glyph + keybinding `tab`).
- **In:** toggle state.
- **When to use:** the single control that flips the drawer (image 2 ↔ image 3).
  The header pill doubles as the affordance (`fred · ●●● 3 running ▾` open / `▸`
  closed).

### `FleetHeader`   `📐 designed-only`
Top bar: wordmark / `≡`, active-agent name + state, fleet counts
(`● 3 online · 2 running`), model label (`Build · Vibeproxy`).
- **In:** `FleetSnapshot` + model. Extends today's header.
- **Look (mockup):** a right-side pill — a **dropdown** `fred · ●●● 3 running ▾`
  that also toggles the drawer; the colored dots summarize the fleet at a glance.
- **When to use:** as the fleet's at-a-glance summary + drawer trigger. Counts are
  **derived** from `FleetSnapshot.agents`, never stored.

### `ProgressRow`   `📐 designed-only`
Columnar task row from the dashboard: `● TITLE` + `StatusChip` + description +
**progress bar** + `% · elapsed`.
- **In:** a task with optional `progress` (0–100).
- **Look (mockup):** `● Index repo dependencies  RUNNING` / `Scanning package
  graphs…` / `▓▓▓▓▓▓░░░  64% · 18:42`. Below the list: `□ 2 completed · …`.
- **When to use:** when a task has a **known % complete** (use the `ProgressBar`).
  When progress is unknown, fall back to `ActivityStatus`/`ActivityGlyph` — don't
  fake a bar. This is the dashboard's per-task row; the pinned single-agent view
  uses `ActivityRegion` instead.

### `StatusBar`  *(exists today — kept)*   `◻ inlined`
Bottom hairline bar; keybinding-hint segments
(`tab switch · / prompt · ctrl+p commands · q quit`).
- **When to use:** persistent global controls/keys + cwd + mode. Drawn in `app.py`.

---

## Composition — the components compose

**The catalog is a kit, not a list of screens.** The fleet mockups are not new
widgets — they are the existing primitives *composed*. Build new surfaces by
assembling these, not by inventing one-offs (Principle: reuse before invent).

```
PersonaIndicator  = StatusChip            reading FleetSnapshot.active_id
AgentRail row     = StateDot + name + status-word + sub-line     (per agent)
FleetHeader pill  = wordmark + derived dots + counts + dropdown  (← same snapshot)
ProgressRow       = StateDot + StatusChip + ProgressBar + elapsed (one task)
Cron task row     = StateDot(□) + title + ScheduleBadge          (a task that is scheduled)
ActivityRegion    = ActivityStatus + (ctrl+o) ToolCallRow×N      (already shipped this way)
SelectModal       ← PermissionModal extends it                   (modal reuse)
AppShell          = SidebarToggle + AgentRail + [main] + StatusBar
```

Two consequences the mockups make concrete:

- **One snapshot, many views.** `FleetHeader` counts, the `AgentRail` roster, and
  the `PersonaIndicator` chip all read the **same** `FleetSnapshot` — header counts
  are *derived*, never stored. Add an agent to the tuple and every surface updates.
- **The same row scales N=1 → N.** An `AgentRail` row is one `StateDot`+chip
  composition; the fleet is that row × N. This is design-system H2 ("the fleet with
  N members; single-agent is N=1") — which is why C2a (N=1 chip) and C2b (the rail)
  reuse one data path instead of two.

When composing, the atoms (`StateDot`, `StatusChip`, `ProgressBar`, `SectionLabel`,
`Hairline`) carry color+glyph+weight; the composite just arranges them. If a new
need can't be met by arranging existing atoms, *that* is when a new catalog entry
is justified (with rationale in the spec).

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
| | `NewPersonaModal` | ✅ shipped | `widgets/new_persona_modal.py`; opened by `n` in the rail (persona-create) |
| | `DecisionModal` | ✅ shipped | `widgets/decision_modal.py`; pushed from `app.py` on a decision meta |
| **—** input/nav | `SlashMenu` | ✅ shipped | wired in `app.py` |
| | `PromptArea` | ✅ shipped | wired in `app.py` |
| | `StatusBar` / footer meta | ◻ inlined | drawn in `app.py` |
| **E** future | `ScheduleBadge` · `CronRow` | 📐 designed-only | no class; `schedule` snapshot field unpopulated |
| **F** shell/nav | `PersonaIndicator` (status-bar chip) | ✅ shipped | C2a, PR #46; reads `FleetSnapshot.active_id` |
| | `AgentRail` (rail / drawer) | ✅ shipped | `widgets/agent_rail.py` (C2b); switching wired in `app.py` (C2c) |
| | `AppShell` · `SidebarToggle` · `FleetHeader` · `ProgressRow` | 📐 designed-only | no class (later fleet phase) |

**Reality check:** only the `✅` rows are usable today. `SlashMenu` / `PromptArea`
ship but were missing from the original A–F grouping — listed here under
input/nav. The `*`/`⭐` markers used in older revisions of this file meant
"planned for the on-ramp," not "exists" — they were aspirational and have been
replaced by the status column above.

When a new UI need arises: **first find the component here; extend it or compose
existing ones; only add a new entry to this catalog (with rationale in the spec) if
nothing fits.**
