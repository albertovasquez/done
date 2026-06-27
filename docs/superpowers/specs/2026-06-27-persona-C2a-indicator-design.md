# Persona C2a — the active-persona indicator (engine seam + status-bar chip)

**Status:** design / spec (ready for writing-plans)
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Part of:** the C2 drawer arc (`2026-06-27-persona-C2-drawer-arc-design.md`). This is
sub-project **C2a** — the indicator and the reusable engine→TUI persona seam.
**Builds on:** C1 (merged) + the TUI design system + the existing `FleetSnapshot`.

---

## 1. Purpose

Answer "what persona am I on?" in the running TUI, and in doing so wire the
engine→TUI persona seam that C2b (rail+switch) and C2c (true fleet) both reuse.

C2a ships a **status-bar persona chip**. It does NOT build the rail, switching, or
any engine multiplexing — those are C2b/C2c. C2a is purely: the engine reports its
resolved persona id → `FleetSnapshot` → one chip.

**Why first:** the chip forces the load-bearing data path (engine push → snapshot →
present) to exist. C2b's rail reads the same snapshot fields; C2c grows the same
tuple. Nothing here is throwaway (see the arc spec's reuse ledger).

---

## 2. The load-bearing rules

- **Engine-truthful (H1).** The chip shows `state.workspace_dir.name` — the persona
  the agent *actually resolved* (the same key C1 uses to read/write the model in
  `done.conf` and resolve persona/memory). It can never disagree with what the agent
  runs as. The TUI does NOT echo `--persona`; the engine reports the truth.
- **Pure display, zero agent-behavior change.** Emitting the chip changes no
  injection, no model, no routing. The byte-identical-behavior guarantee for the
  agent is untouched — the chip just reads back what is already true.
- **Reuse, don't reinvent.** The emit reuses the existing `with_meta` _meta channel;
  the parse extends `harness_chips`; the destination (`FleetSnapshot.active_id`)
  already exists. No parallel data path.
- **Always shows something.** Unlike `persona_load` (gated on `injected` so the empty
  default stays silent), the `persona` chip emits for EVERY persona including
  `default` — an identity indicator must always show the truth.

---

## 3. Data flow

```
prompt(session_id) — after the task_classified emit (acp_agent.py:195-196):
  if not state.persona_emitted:
      pid = state.workspace_dir.name if state.workspace_dir else "default"
      await conn.session_update(session_id,
          with_meta(message_chunk(""), {"persona": {"id": pid}}))
      state.persona_emitted = True
   │  ACP session/update _meta  (TuiClient → messages.SessionUpdate → app.on_session_update)
   ▼
render.harness_chips(field_meta) / render_update — parse {"harness":{"persona":{"id":"fred"}}}
   ▼
state.reduce(snapshot, persona_event) → FleetSnapshot(active_id="fred",
                                          agents=(AgentSnapshot(id="fred", name="fred"),))
   ▼
app posts FleetUpdated → _status_right() refresh → persona StatusChip shows "fred"
```

**Timing:** the chip appears after the **first turn's classification** (when the
engine first emits it), exactly like `task_classified` today. On LANDING (before any
prompt) the chip is **absent / neutral placeholder** — engine-truthful: nothing has
reported the id yet. (C2b may seed it eagerly; C2a does not guess.)

**Emit placement:** right after the `task_classified` emit (acp_agent.py:195-196),
BEFORE the gated `persona_load` block (acp_agent.py:198-209). Ordering: classified →
persona → persona_load → memory_load. The `persona` chip is NOT gated on
`personalized` or `injected` — it fires on every turn-one regardless of dispatch path
(chat/agent/clarify/ambiguous), because the indicator must show for every session.

---

## 4. Components (files & responsibilities)

All additive — extending existing files at established seams.

```
harness/acp_agent.py        EMIT. In prompt(), after the task_classified emit and
  before the persona_load block, add the persona chip:
      if not state.persona_emitted:
          pid = state.workspace_dir.name if state.workspace_dir else "default"
          await self._conn.session_update(session_id,
              with_meta(message_chunk(""), {"persona": {"id": pid}}))
          state.persona_emitted = True
  NOT gated on injected/personalized (unlike persona_load) — always emits once/session.

harness/acp_session.py      One flag. Add `persona_emitted: bool = False` to
  SessionState (mirrors persona_load_emitted). The once-per-session gate.

harness/tui/render.py       PARSE. Extend harness_chips (render.py:64-83): add a
  `persona` branch mirroring the task_classified parse (render.py:71-77) —
  harness.get("persona"); if a dict with a str "id", surface it. Also expose the id
  to the reducer path (render_update), as the decision chip already does.

harness/tui/state.py        REDUCE. A `persona` ReducerEvent writes the id into
  FleetSnapshot.active_id AND the active AgentSnapshot.id/name. The fields already
  exist (active_id, active_agent(); state.py hardcodes "default" today). Idempotent
  on a repeated id. THIS is the reuse-locked seam (comment: C2b/c read these fields).

harness/tui/widgets/status_chip.py   CHIP. A persona StatusChip for the status bar.
  StatusChip already exists (mode/model/for_yolo). Add a persona instance/variant
  (e.g. a for_persona constructor or a labeled StatusChip) with a persona/≡ glyph
  from the theme token map.

harness/tui/app.py          MOUNT. In the status-bar mount block (app.py:223-226),
  add the persona chip beside the model/mode chips; refresh it on FleetUpdated via the
  existing _status_right() path (app.py:245). One integration edit.
```

**Not touched in C2a:** `AppShell`, `AgentRail`, `SidebarToggle`, `list_personas()`
wiring, switching, engine multiplexing. (C2b/C2c.)

---

## 5. Error handling

| Case | Behavior |
|---|---|
| `state.workspace_dir is None` (no persona) | id → `"default"` (same fallback `_persona_key` uses). Chip shows `default`. |
| `persona` chip malformed / no `id` | `harness_chips` skips it (existing `isinstance` guards). Chip keeps prior value / placeholder. Never raises. |
| LANDING (before first turn) | chip absent / neutral placeholder — no false claim. |
| repeated `persona` event, same id | `reduce` is idempotent — same `active_id`, no flicker. |
| `session_update` for the chip dropped | best-effort like every other chip; the chip just doesn't update. No crash. |

**Truth invariant:** the chip equals `workspace_dir.name`, the exact key C1 uses for
the model + persona + memory. The indicator cannot disagree with what the agent runs
as — the reason for an engine push over a TUI echo.

---

## 6. Testing strategy

Pure units exhaustively tested; the emit, parse, reduce, and render each isolatable.

- **`tests/test_acp_agent.py`** (extend) — the agent emits a `persona` chip with
  `{id: workspace_dir.name}` once per session, after `task_classified`; emits for
  `default` too (NOT gated on injected); does NOT re-emit on the second turn
  (`persona_emitted` gate); `None` workspace → id `"default"`; fires on the
  clarify/ambiguous path too (unlike persona_load).
- **`tests/test_tui_render.py`** (extend) — `harness_chips` parses a `persona` chip →
  surfaces the id; malformed / missing-`id` chip is skipped, no raise.
- **`tests/test_tui_state.py`** (extend) — `reduce` with a `persona` event writes
  `active_id` + the active `AgentSnapshot.id`/`name`; idempotent on repeat. (The
  reuse-locked field — comment notes C2b/c read it.)
- **`tests/test_tui_pilot.py`** (extend) — the status bar renders a persona chip
  showing the id after a turn; absent / neutral on LANDING.
- **Truth lock** — a test asserting the chip's id equals `workspace_dir.name`, and
  that emitting the chip changes no agent behavior (pure display).

Full suite stays green; net-new tests cover emit + parse + reduce + render.

---

## 7. Definition of done (C2a)

- The running TUI shows the active persona as a status-bar chip, after the first turn.
- The id is sourced from the engine's real resolved `workspace_dir.name`, via
  `FleetSnapshot.active_id` (NOT echoed from `--persona`).
- Default/None → `default`; malformed chips degrade silently; LANDING shows no false
  claim.
- The seam (`persona` chip → `harness_chips` → `reduce` → `FleetSnapshot`) is wired so
  C2b's rail and C2c's fleet read the same fields with no rework.
- Full suite green; emit/parse/reduce/render each tested.
