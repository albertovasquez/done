# Persona C2 — the TUI persona drawer (arc design: a → b → c)

**Status:** umbrella design / arc spec. C2a is fully specified here + in its own
sub-spec; C2b and C2c get their own brainstorm→spec→plan cycles when reached.
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Builds on:** C1 (persona selection & isolation core — merged, PR #40 + #41) and the
TUI design system (`docs/superpowers/specs/2026-06-26-tui-design-system-design.md`,
which already specs `AppShell`/`AgentRail`/`SidebarToggle`/`FleetHeader` and the
`FleetSnapshot` data model).
**Tracker:** issue #29 (the "multi" — selection UI was the deferred half of Phase C).

---

## 1. Purpose

C1 made the engine multi-persona (`--persona`, per-persona isolation, per-persona
model) but shipped **no UI** — there is no in-app way to see or switch the active
persona. C2 builds the TUI surface: the **AppShell + AgentRail drawer/aside** from
the design system, plus selection/switching, plus (ultimately) the true concurrent
fleet the mockups show.

This is large and two-natured — a UI build *and* an irreversible engine change — so
it is decomposed into **three sub-projects**, designed as one coherent arc so the
seams line up, each shippable on its own:

| Sub | Delivers | Engine change | Depends on |
|---|---|---|---|
| **C2a — Indicator** | engine emits its persona id → `FleetSnapshot` → a status-bar persona chip | none | C1 |
| **C2b — Rail + switcher** | `AppShell` + `AgentRail` listing all personas (active highlighted), select → switch the session | none | C2a's seam |
| **C2c — True fleet** | one process serving N concurrent personas, live state dots | **yes — own brainstorm + Codex review** | C2b's UI + C2a's snapshot |

**The iteration thesis (why a → b → c, not all-at-once):** the indicator forces the
load-bearing data path to exist — *engine reports its real persona id →
`FleetSnapshot.active_id` → presentation*. C2b's rail reads the **same**
`FleetSnapshot.agents`; C2c's fleet just grows that tuple from N=1 to N>1. Each step
is additive; nothing built earlier is reworked. This is exactly the design system's
H2 ("the TUI is the fleet with N members; single-agent is N=1") and M1 (the pure
`FleetSnapshot` reducer) paying off.

---

## 2. The shared data model (the seam all three reuse)

The single channel, all of it already existing and tested in some form:

```
acp_agent.prompt()  — emits a `persona` _meta chip ({id: workspace_dir.name})
   │  ACP session/update _meta   (the SAME channel task_classified / persona_load /
   │                              skill_load already use — with_meta(...))
   ▼
state.persona_from_meta()  — NEW pure parser (mirrors the EXISTING decision_from_meta
                             → DecisionOpened → _apply structured path; NOT
                             harness_chips, which only yields transcript strings)
   ▼
state.reduce(PersonaResolved(id))  — sets FleetSnapshot.active_id + ensures an active
                  AgentSnapshot (FleetSnapshot already HAS active_id + the `.active`
                  property; today active_id is hardcoded "default" — C2a populates it).
                  This is a top-level reduce change (active_id/tuple membership), not
                  just a per-agent fold.
   ▼
presentation  — C2a: a dedicated #statusbar-persona Static.  C2b: the AgentRail reads
                FleetSnapshot.active_id for highlighting.  C2c: the tuple grows to N.
```

**Engine-truthful (design-system H1):** the engine reports the persona it actually
*resolved* (`workspace_dir.name`), never what the TUI guessed from `--persona`. So an
unknown-id error, a defaulted launch, or a future server-chosen persona all show the
truth. This is why the data path is an engine→TUI push, not a TUI-side echo of the
launch flag (an echo would bypass `FleetSnapshot`, forcing a rebuild at C2b).

**The reuse ledger (honest — corrected after a Codex review of the live code):**

| C2a builds | C2b reuses as | C2c reuses as |
|---|---|---|
| `persona` _meta emit | same emit, per agent | same, N agents |
| `persona_from_meta` + `PersonaResolved` event + reduce case | same parser/event path | same, fans out to N |
| `FleetSnapshot.active_id` populated from real id | rail reads it to HIGHLIGHT the active one | highlights among N |
| `#statusbar-persona` chip | chip stays; rail added beside it | unchanged |

**What C2a does NOT give C2b for free (the honest gap):** C2a only ever populates the
**active** agent in `FleetSnapshot.agents` (the engine reports one id — its own). The
rail must list **all** personas, so **C2b still needs separate `list_personas()`
wiring** to build the non-active rail entries, then merges active-highlighting from
`active_id`. The reused part is the persona-event path + `active_id`; the rail's full
roster is new C2b work. (C2c then replaces `list_personas()`-static entries with N
live `AgentSnapshot`s from N real sessions.)

---

## 3. Sub-project boundaries

### C2a — Indicator (fully specified; see the C2a sub-spec)
Engine emits a `persona` chip once per session; `harness_chips` parses it; `reduce`
writes `FleetSnapshot.active_id`/active `AgentSnapshot`; a status-bar `StatusChip`
shows the id. **No rail, no switching, no engine multiplexing.** Purely: engine
reports id → snapshot → one chip. Fits today's one-process-one-persona engine with
zero behavior change to the agent (pure display).

### C2b — Rail + switcher (own spec when reached)
`AppShell` (the two-rail responsive frame, collapses to today's single column at
N=1) + `AgentRail` (lists `list_personas()`, active highlighted via
`FleetSnapshot.active_id`) + `SidebarToggle`. Selecting a persona **switches the
session** to it — re-point/re-launch the single agent at that workspace (the engine
stays one-persona-at-a-time, matching C1). The rail reads the C2a snapshot fields.
Still **no engine multiplexing** — one persona runs at a time; switching is
between-sessions (consistent with C1's first-turn-only injection + "switch between
sessions" position). Open question for its spec: switch = new session vs re-exec;
friendly names from `persona.toml`.

### C2c — True fleet (own brainstorm + Codex review when reached)
One process (or N) serving **N concurrent personas** with live per-agent state dots —
the literal mockup. **The foundational fork is deferred to this sub-spec, with Codex
review, because it is irreversible engine work:**
- **In-process N-sessions:** one `acp_agent`, `new_session` takes a persona/workspace
  arg (today hardcodes `self._workspace_dir`), `SessionStore` keys by persona, model
  factory resolves per-session. Reuses C1's per-session `workspace_dir` pipe; cooperative
  concurrency unless true parallel loops are added.
- **N-subprocess:** TUI spawns one `acp_main` per persona (each already correctly
  single-persona), multiplexes N ACP streams + lifecycles into one rail. Heavier
  client, simpler engine, genuine parallelism.

This decision is the analog of C1's single-home-model choice and gets the same
treatment: its own brainstorm, an explicit precedence/lifecycle design, and Codex
adversarial review before any code. C2c does NOT block C2a/C2b.

**C2c watch-for (flagged by the C2a whole-branch review):** C2a's `reduce()`
`PersonaResolved` case renames the active agent's `id` in place
(`replace(a, id=event.id, ...)`). This is correct at N=1, but in a multi-agent tuple
it can produce two agents sharing one id if `active_id` resolves to an id another agent
already holds (reproduced: `[a,b]` active="a" + `PersonaResolved("b")` → `['b','b']`).
Unreachable in C2a/C2b (single active agent), but C2c MUST restructure this — key the
fold on a stable, immutable `agent_id` rather than mutating `id`, or de-dup after the
remap. Address it in C2c's reducer design, not before (YAGNI).

---

## 4. Constraints inherited from the design system

- **H1 — engine emits, TUI owns all presentation.** The persona seam is an engine
  push; all chip/rail rendering is TUI-side.
- **H2 — fleet-shaped, correct at N=1.** Every C2 component is built against
  `FleetSnapshot` (a tuple of N agents); C2a/b run it at N=1.
- **M1 — pure reducer.** Persona events flow through `state.reduce()` (pure,
  exhaustively testable); widgets read snapshots, never compute state.
- **Brand voice = animation policy (H4).** Any rail/chip motion is restrained (one
  looping glyph max, ≤250ms transitions, reduced-motion fallback).
- **Build on what works (H3).** Extend `render.py`, `state.py`, `app.py`,
  `status_chip.py`, the `harness` theme — no rewrites.

---

## 5. Definition of done (the arc)

- **C2a:** the running TUI shows which persona you're on (a status-bar chip), sourced
  from the engine's real resolved id, via `FleetSnapshot`. (This sub-spec.)
- **C2b:** the AgentRail lists all personas, highlights the active one, and switching
  works between sessions.
- **C2c:** N personas run and display concurrently with live state — after a dedicated
  engine-structure decision + Codex review.

Each sub ships its own spec + plan + PR; this doc is the arc they conform to.
