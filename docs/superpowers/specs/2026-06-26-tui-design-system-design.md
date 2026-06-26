# TUI design system — decisions, agent-state model & component catalog

**Status:** design / spec (no implementation in this doc)
**Date:** 2026-06-26
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Scope:** the DoneDone (`dn`) Textual TUI — a design system built *on top of* the
existing client, scaling to the persona-fleet mockups.
**Companion artifact:** `harness/tui/styles/components.md` (the component catalog).
**References studied:** `Brand Book - DoneDone.pdf` (p.10 voice, p.12 palette),
the DoneDone fleet mockups, the "grill-me" decision UI and live task-tree
screenshots, and the existing TUI (`harness/tui/{app,render,messages,theme}.py`,
`app.tcss`).
**Related:** `docs/superpowers/specs/2026-06-26-persona-fleet-design.md` (the
engine-side track this UI scales toward).

---

## 1. Purpose

Turn a large research/design exploration into a **committed, layered set of
decisions** plus a **reusable visual-component catalog** for the `dn` TUI, grounded
in *what the engine actually produces* and built **on top of** what already works —
not a rewrite.

The engine produces four kinds of thing, and the entire system is organized around
them:

1. **Responses** — streamed agent text.
2. **Work-in-progress** — tool calls, activity, progress.
3. **Decisions needed** — permission requests and clarification ("grill-me")
   prompts.
4. **Future / scheduled signals** — crons, timers, "something will happen later."

The target visual is the fleet mockups (an AGENTS rail, per-agent status, per-task
progress + elapsed, a conversation pane that swaps with the selected agent). The
engine is single-agent today; the persona fleet is a parallel roadmap. This design
therefore **designs for the fleet but builds single-agent-ready** (§3, H2).

This document does not write code. It is the brainstorm→spec step; a writing-plans
cycle follows.

---

## 2. What exists today (build on this, don't throw it away)

The current TUI already has the seams this system needs:

| Existing piece | Role | What the system does with it |
|---|---|---|
| `render.py` — pure render core (`render_update` → `RenderedItem`, `harness_chips`) | Turns ACP update objects into display-ready values. No Textual, no async. | **Kept as the upstream of the reducer.** The model's purity mirrors it. |
| `messages.py` — typed handoff (`SessionUpdate`, `PermissionRequest`) | Marshals ACP callbacks to the app, carries `gen`/`session_id` freshness filters. | **Kept.** Adds one message (`FleetUpdated`). The `gen`/`session_id` guards are load-bearing and stay. |
| `theme.py` — one named `Theme` + `variables` + `STATUS_COLOR` | Single semantic-token source; already cites the brand book (p.13). | **Extended in place** (M2). The one source of truth for all tokens. |
| `app.py` `HarnessTui` — two states (LANDING / CONVERSATION), streaming Markdown, `_show_working`, slash menu, modals | The app shell + integration point. | **One integration point edited.** Streaming-Markdown path and gen/session guards untouched; the flat status/tool lines are replaced by widgets. |
| `app.tcss` — `.compose`, `#transcript`, `#statusbar`, modal rules | Layout + theming. | **Extended** with component classes and the shell layout; existing rules stay. |
| `PermissionModal` / `SelectModal` | Existing decision modals. | **Kept and catalogued** as the siblings of `DecisionPrompt`. |

The architectural conclusion matches the persona-fleet doc's: **almost nothing here
needs a new architectural seam.**

---

## 3. The layered decisions

### High-level (philosophy — rarely revisited)

- **H1 — The engine emits semantic events; the TUI owns 100% of presentation,
  animation, and state-mapping.** This is the existing `render.py` boundary, made
  into law. No animation or layout logic ever leaks into the engine/ACP layer.
- **H2 — The TUI is "the fleet with N members."** Single-agent today is `N=1`.
  Every component is fleet-shaped but correct at `N=1`, degrading gracefully to
  today's single-conversation layout.
- **H3 — Build on what works, never throw away.** `render.py`, `messages.py`, the
  `harness` theme, the two-state app — all kept. The system *adds a layer*.
- **H4 — Brand voice = animation policy.** The brand book's tone — *"Minimalista,
  directo y con actitud. La marca no necesita gritar, pero cuando habla todos
  escuchan"* (p.10) — **is** the restraint rule: motion communicates a state change,
  never decoration. Exactly **one looping animation** on screen (the active-work
  glyph); transitions ≤250ms ease-out; success/fail = a brief border flash then
  settle; never animate during heavy streaming (don't fight the text); every effect
  has a reduced-motion + monochrome fallback.
- **H5 — Design around what the engine produces** (the four categories in §1).
  These are the column headers of the whole system.

### Mid-level (architecture — stable, occasionally revised)

- **M1 — Insert a presentation layer.** A pure, Textual-free `AgentState` reducer
  folds `RenderedItem`s + meta into a `FleetSnapshot`; widgets are dumb and reactive
  against it (the "ViewModel + widget catalog" approach). State transitions live in
  exactly one place — the reducer — so they are exhaustively unit-testable without a
  running app, like `render.py` today.
- **M2 — One design-token source of truth.** Extend `theme.py` (`variables` +
  `STATUS_COLOR`). The style guide (`components.md`) documents the tokens. No
  hardcoded hex outside `theme.py` / `COLORS`.
- **M3 — The catalog is independently testable widgets.** Each has a defined input
  (a snapshot slice), one purpose, and a snapshot/pilot test.
- **M4 — Two-sidebar responsive shell.** A frame of `[left rail][main][right
  rail][status bar]` with two collapsible sidebars + toggle affordances. Collapses
  to single-column (today's view) at `N=1` / narrow width.
- **M5 — Capability tiers for richness.**
  - *Layer 1 (mandatory, everywhere):* Textual-native — CSS, workers (`run_worker`),
    timers (`set_interval`), `styles.animate`, `ProgressBar`, `LoadingIndicator`.
  - *Layer 2 (optional):* Unicode canvas niceties (half-block / Braille effects).
  - *Layer 3 (capability-flagged, off by default):* terminal image protocols
    (Kitty / Sixel via `textual-image`), Unicode fallback.
  - **Skia is rejected** — it does not render to a cell grid and adds a heavy GUI
    dependency for no terminal-first benefit.

### Right-now (the immediate on-ramp)

- **N1 — Land the token + style-guide foundation first** (M2). Lowest risk,
  unblocks everything, visible improvement immediately.
- **N2 — Build the `AgentState` reducer + `FleetSnapshot`** (M1) as a pure module
  with exhaustive tests, fed from the existing `on_session_update`.
- **N3 — Ship the headline widgets the screenshots demand** —
  `ActivityStatus`+`TaskTree` (live label · elapsed · tokens over an updating
  checklist) and `DecisionPrompt` (the "grill-me" UI) — against today's
  single-agent stream. Immediate UX win, no fleet required.
- **N4 — Resolve the palette tension before any status color ships** (done in §4.1:
  green/amber adopted as sanctioned product-status tokens).
- **N5 — Defer the sidebars / fleet rail** until the persona-fleet engine work
  (Phases A–C) provides their data, but design their interfaces now so N3's widgets
  slot into them later.

One line: **High = laws · Mid = architecture · Now = a 4-step on-ramp (tokens →
reducer → two widgets → palette) that improves today's TUI and lays fleet rails
without needing the fleet.**

---

## 4. Design tokens

### 4.1 The palette tension and its resolution

The mockups use **green = completed** and **amber = cron/scheduled**. The brand book
(p.12) defines exactly five colors and **neither green nor amber**:

| Brand token | Hex | Role |
|---|---|---|
| brand blue | `#286CE9` | accent / active / running / primary |
| light grey | `#E3E3E3` | foreground text |
| slate grey | `#8690A3` | secondary / muted |
| dark navy | `#0A1524` | background |
| red-orange | `#E02F07` | error / failed |

**Decision (N4):** adopt **one green and one amber as official product-UI status
tokens** — a documented, intentional *extension* of the brand book for product
surfaces where go / caution / future semantics matter. This blesses what `theme.py`
already does pragmatically, but reframes it: these are sanctioned product tokens, not
"off-brand colors kept for legibility." Status is then carried by **color + glyph +
weight together**, so done-vs-running is unambiguous even in monochrome terminals.

### 4.2 Color tokens (extend `HARNESS_THEME.variables` + `STATUS_COLOR`)

```
# Brand core (unchanged)
accent      #286CE9     fg        #E3E3E3     slate/muted  #8690A3
bg          #0A1524     surface   #16243A     error        #E02F07

# Product status (sanctioned extension)
success/done    #7ee787
scheduled/attn  #e3b341

# Derived (existing + new)
muted-deep  #5B6577     code  #9DB8E8     accent-30  (#286CE9 @ 30%)
```

### 4.3 Glyph tokens (the iconography vocabulary — one map)

```
state dots      idle •   active ◐(anim)   responding ▌
terminal        done ✓   failed ✗
future/decide   scheduled ⏱   awaiting ?  / ▌pulse
brand           ≡ mark
tool subtypes   edit ✎   test ⚑   read ◇   shell $   search ⌕
```

### 4.4 Spacing / motion / type tokens

- Transition budget: **≤250ms, ease-out**. Exactly one looping animation (active
  `◐`). Success/fail: brief border flash, then settle.
- Section labels: tracked **bold caps** (echoing the brand's `- NUESTRO PROPÓSITO -`
  and `AGENTS` / `CURRENT TASKS` mockup headers).
- Hairline footer rule (brand grammar) for the status bar.
- Terminals can't load the brand display font; **weight + tracking + color** carry
  the brand's typographic hierarchy (this is already the convention in
  `SelectModal #select-title`).

All tokens live in `theme.py` (M2) and are documented in `components.md`.

---

## 5. The agent-state event model

The vocabulary every component subscribes to. **Engine-truthful**: every state is
derivable from a signal the engine already emits.

### 5.1 States (per agent)

```
idle                 no active turn (landing, or turn finished)
thinking             prompt sent, no token/tool yet     ← _show_working() moment
responding           streaming an answer                ← _stream_message()
running_tool         a tool call is live                ← ToolCallStart/Progress
    ToolStatus       pending | active | done | failed
    subtype          shell | edit | test | read | search   (INFERRED, glyph-only)
awaiting_permission  permission request open            ← PermissionRequest
awaiting_decision    clarification needed ("grill-me")  ← field_meta["harness"] chip
scheduled            future / cron / timer pending      ← fleet phase E (designed now)
done                 turn ended cleanly (end_turn)
failed               turn ended in error / disconnect
```

Two honesty rules (both settled in brainstorming):

- **`subtype` is inferred for glyphs/labels only**, with a neutral `shell` fallback.
  The TUI guesses `edit`/`test`/`read` from the command string purely to pick an
  icon; inference lives entirely in the pure display layer and never asks the engine
  to classify (H1).
- **`awaiting_decision` is recognized from a `field_meta["harness"]` chip now** —
  the same mechanism `harness_chips()` already parses for `task_classified` /
  `skill_load`. This lets `DecisionPrompt` ship and be tested against today's stream;
  it swaps to a formal ACP signal later with **zero widget changes**.

### 5.2 Data structures (pure, Textual-free — `harness/tui/state.py`)

```python
@dataclass(frozen=True)
class AgentSnapshot:
    id: str                        # "default" today; persona id later
    name: str                      # display name ("fred")
    state: AgentState
    tool: ToolView | None          # current tool: title, status, subtype
    activity_label: str            # "Asking clarifying questions…" / "Editing api.py"
    elapsed: float                 # seconds since turn start  (from _turn_start)
    tokens: int                    # token counter             (from _tokens)
    tasks: tuple[TaskItem, ...]    # the live checklist (TaskTree)
    schedule: ScheduleView | None  # next cron/timer, if any
    decision: DecisionView | None  # open clarification, if any

@dataclass(frozen=True)
class FleetSnapshot:
    agents: tuple[AgentSnapshot, ...]
    active_id: str                 # which agent the main pane shows
    # header counts (running / scheduled / idle) are DERIVED, not stored
```

Supporting value types: `ToolView`, `TaskItem`, `ScheduleView`, `DecisionView`,
`AgentState` (enum), `ToolStatus` (enum).

### 5.3 The reducer (the one new pure module)

```python
def reduce(prev: FleetSnapshot, event: ReducerEvent) -> FleetSnapshot: ...
```

`ReducerEvent` is a small union fed from the app's existing callbacks: a
`RenderedItem` (from `render_update`), a meta/chip dict, a turn-start, a turn-end, a
token-usage update, a permission open/close. **The reducer is the only place state
transitions live** — pure, synchronous, no Textual — so it is exhaustively
unit-testable with plain dataclasses (like `render.py` / `events.py` today). Widgets
never compute state; they read a snapshot.

### 5.4 Data flow

```
ACP stream → TuiClient → messages.SessionUpdate
   → app.on_session_update            (gen / session_id guards KEPT)
       → render.render_update → RenderedItem          [existing, pure]
       → state.reduce(snapshot, event) → FleetSnapshot [NEW, pure]
       → post messages.FleetUpdated
   → widgets read their snapshot slice & re-render     [NEW, dumb / reactive]
   (AnswerStream text path stays on the existing _stream_message)
```

The win: `scheduled`, `awaiting_decision`, multi-agent, elapsed/token activity, and
the task tree all become **fields on a snapshot** instead of new ad-hoc code paths.
A fleet of N agents is a tuple of N `AgentSnapshot`s.

---

## 6. The component catalog (summary)

Full interfaces and ASCII previews live in `harness/tui/styles/components.md`. Each
component is a dumb, reactive widget reading a snapshot slice, with one purpose and a
snapshot/pilot test (M3). Grouped by the four engine outputs (H5):

```
A primitives   StatusChip (+ for_yolo footer mode chip) · StateDot/ActivityGlyph · Hairline/SectionLabel
B responses    AnswerStream* · UserMessage*
C work         ActivityStatus⭐ · TaskTree⭐ · ToolCallRow · ProgressRow
D decisions    DecisionPrompt⭐ · PermissionModal* · SelectModal*
E future       ScheduleBadge · CronRow
F shell/nav    AppShell · AgentRail · SidebarToggle · FleetHeader · StatusBar*

   * = exists today, kept/promoted     ⭐ = headline new, ships in N3
```

~18 components, but only ~6 are net-new code for the immediate on-ramp
(`ActivityStatus`, `TaskTree`, `ToolCallRow`, `DecisionPrompt`, `StatusChip`,
`ActivityGlyph`); the rest are existing widgets promoted into the catalog or
fleet-shell pieces designed-now/built-later.

Headline behaviors:

- **`ActivityStatus`** — `· <label>… (1m 18s · ↓ 4.0k tokens)`; the single looping
  glyph + a `set_interval` elapsed tick. Supersedes today's bare `LoadingIndicator`.
- **`TaskTree`** — nested checklist (`✓` done / `▣` in-progress / `□` pending),
  updated in place. Driven by the tool-call sequence initially; by real plan/subtask
  events later.
- **`DecisionPrompt`** — the "grill-me" UI: question + numbered options
  (**title + dimmed rationale**) + `Type something` / `Chat about this` fallbacks.
  **One model, two render targets:** inline in the transcript for refinement
  questions; escalates to a modal only when the agent is truly blocked. Keyboard
  driven; the answer persists in the transcript as a record.

**Sidebars (M4):** the spec locks the toggleable two-rail `AppShell` + `SidebarToggle`
as the frame, and **recommends** right = `AgentRail` (per mockups), left =
crons/schedule — marked adjustable per phase, since that data lands with the fleet.

---

## 7. Implementation locations (the seams)

Every change is **additive** — new files plus thin routing edits. Nothing rewritten
(H3).

### New files

```
harness/tui/state.py            NEW — pure: AgentState, AgentSnapshot, FleetSnapshot,
                                  ToolView/TaskItem/ScheduleView/DecisionView,
                                  reduce(), subtype inference. No Textual, no async.
harness/tui/styles/             NEW dir
  components.md                   the catalog (standing reference)
  tokens.tcss                     (optional, later) token partial split from app.tcss
harness/tui/widgets/            EXISTING dir — add:
  status_chip.py                  StatusChip, StateDot, ActivityGlyph
  activity_status.py              ActivityStatus  ⭐
  task_tree.py                    TaskTree        ⭐
  tool_call_row.py                ToolCallRow
  decision_prompt.py              DecisionPrompt  ⭐ (inline + modal targets)
  schedule_badge.py               ScheduleBadge / CronRow   (designed now)
  agent_rail.py                   AgentRail / SidebarToggle (designed now)
```

### Edited files (thin, additive)

- **`theme.py`** — add status/glyph/spacing/motion tokens to
  `HARNESS_THEME.variables` + `STATUS_COLOR`; re-document green/amber as sanctioned
  product-status tokens.
- **`render.py`** — extend `RenderedItem` / `render_update` only if new fields are
  needed (e.g. surfacing the decision chip). The pure core remains the reducer's
  upstream.
- **`app.py`** — the integration point. `on_session_update` keeps its `gen` /
  `session_id` guards and routes events through `reduce()` into a held
  `FleetSnapshot`, posting `FleetUpdated` to the reactive widgets.
  `_show_working` / `_write_meta` / the flat tool-line block are **replaced by**
  `ActivityStatus` / `TaskTree` / `ToolCallRow`. The streaming-Markdown path
  (`_stream_message`) is untouched.
- **`app.tcss`** — add component classes and the `AppShell` rail layout (collapsed
  by default); existing rules stay.
- **`messages.py`** — add a `FleetUpdated` message (snapshot handoff); existing
  messages unchanged.

### Test seams (mirror existing test files)

- `tests/test_tui_state.py` — **NEW**, the big one: exhaustive `reduce()` transition
  tests, pure, no Textual (like `tests/test_events.py` / `test_tui_render.py`).
- `tests/test_tui_pilot.py` — extend with the new widgets.
- Snapshot tests (`pytest-textual-snapshot`, optional) for stable states: idle /
  thinking / responding / running_tool / awaiting / done / failed.

The whole system lands on existing seams: a pure core (`render.py` → now also
`state.py`), a typed message handoff (`messages.py`), and one integration point
(`app.py`). **No new architectural seam.**

---

## 8. Phased build sequence & acceptance

Each phase is independently shippable and improves the current single-agent TUI.

### Phase 1 — Tokens & style-guide foundation (N1)
Extend `theme.py`; write `harness/tui/styles/components.md`; restyle existing
widgets onto the documented tokens.
**Acceptance:** no hardcoded hex outside `theme.py`/`COLORS`; green/amber documented
as product-status tokens; existing tests green; visible consistency improvement.

### Phase 2 — The presentation layer (N2)
Build `state.py` (`AgentState`, snapshots, `reduce()`, subtype inference) + a
`FleetUpdated` message. Route `on_session_update` through it. `N=1` snapshot.
**Acceptance:** exhaustive `test_tui_state.py` covers every transition; `gen` /
`session_id` guards preserved; streaming-Markdown path unchanged; no UI freeze
(work stays on `run_worker`).

### Phase 3 — Headline widgets (N3)
`StatusChip` / `ActivityGlyph`, `ActivityStatus` + `TaskTree`, `ToolCallRow`,
`DecisionPrompt` (inline target). Wire `awaiting_decision` to the meta chip.
**Acceptance:** the live activity line shows label · elapsed · tokens with exactly
one looping glyph; tool calls render as subtype-glyph rows with status chips; a
clarification renders inline with numbered options + dimmed rationale + free-text /
chat fallbacks and is keyboard-navigable; reduced-motion fallback verified;
`DecisionPrompt` escalates to a modal when marked blocking.

### Phase 4 — Fleet shell (N5, gated on persona-fleet Phases A–C)
`AppShell` two-rail frame + `SidebarToggle`, `AgentRail`, `FleetHeader`,
`ScheduleBadge` / `CronRow`. Right = AgentRail, left = crons/schedule (adjustable).
**Acceptance:** at `N=1` / narrow width the shell collapses to today's
single-column layout with no rails; at `N>1` the rail lists agents with state dots /
chips and switches the active pane; header counts derive from the snapshot.

### Optional — Layer 2/3 richness (M5)
Unicode-canvas completion effect; capability-flagged terminal images.
**Acceptance:** off by default; degrades to Unicode/blank in unsupported terminals;
never required for any status or completion meaning.

---

## 9. Summary

- **One layer added, nothing thrown away.** A pure `state.py` reducer →
  `FleetSnapshot` → dumb reactive widgets, on top of the existing pure core, typed
  messages, theme, and two-state app.
- **Engine-truthful state model**; `subtype` inferred for glyphs only;
  `awaiting_decision` recognized from a meta chip until a formal signal exists.
- **Brand voice is the animation policy** — restrained, meaningful motion; one
  looping glyph; reduced-motion fallbacks.
- **Palette tension resolved:** green + amber adopted as sanctioned product-status
  tokens; status carried by color + glyph + weight.
- **~18-component catalog** (in `components.md`), ~6 net-new for the on-ramp; two
  headline widgets (`ActivityStatus`+`TaskTree`, `DecisionPrompt`) ship against
  today's single-agent stream.
- **Designed for the fleet, built single-agent-ready** — the shell collapses to
  today's view; a fleet is a tuple of snapshots.
- **Four phases**, each shippable: tokens → reducer → headline widgets → fleet shell.
