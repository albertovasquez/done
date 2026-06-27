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
  the parse + event + reduce mirror the proven `decision_from_meta → DecisionOpened →
  _apply` structured path (NOT `harness_chips`, which only makes transcript strings);
  the destination (`FleetSnapshot.active_id`) already exists. No parallel data path.
- **Always shows something.** Unlike `persona_load` (gated on `injected` so the empty
  default stays silent), the `persona` chip emits for EVERY persona including
  `default` — an identity indicator must always show the truth.

---

## 3. Data flow

**IMPORTANT (corrected after a Codex review against live code):** there is NO path
today from `render.harness_chips` to `state.reduce`. `harness_chips` returns
`list[str]` that `app.on_session_update` appends to the transcript as muted lines
(app.py:780-781); `render_update` ignores `field_meta` entirely. The ONLY structured
chip→reducer path is the **decision** one: `state.decision_from_meta(field_meta)` →
`app._apply(DecisionOpened(view))` (app.py:777-779). C2a MUST mirror that path, NOT
extend `harness_chips` (which would also leak the persona into the transcript and hit
the empty-meta-chunk→RESPONDING wart). Likewise `FleetUpdated` (messages.py:35) is a
DEAD message class — never posted or handled; the app updates presentation directly
via `_apply()` + a status refresh, not via `FleetUpdated`.

```
prompt(session_id) — after the task_classified emit (acp_agent.py:195-196):
  if not state.persona_emitted:
      pid = state.workspace_dir.name if state.workspace_dir else "default"
      await conn.session_update(session_id,
          with_meta(message_chunk(""), {"persona": {"id": pid}}))
      state.persona_emitted = True
   │  ACP session/update _meta  (TuiClient → messages.SessionUpdate → app.on_session_update)
   ▼
state.persona_from_meta(field_meta) — NEW pure parser (mirrors decision_from_meta):
   {"harness":{"persona":{"id":"fred"}}} → "fred" (else None). Guards isinstance.
   ▼
app.on_session_update: pid = persona_from_meta(field_meta); if pid: self._apply(PersonaResolved(pid))
   (added beside the existing decision_from_meta call at app.py:777-779; does NOT go
    through harness_chips, so nothing is appended to the transcript)
   ▼
state.reduce(snapshot, PersonaResolved(pid)) → sets active_id=pid AND ensures an
   AgentSnapshot(id=pid, name=pid) is the active member (see §4 — this is a
   TOP-LEVEL reduce change, since reduce() today never alters active_id or tuple
   membership; _reduce_agent alone cannot do it).
   ▼
app refreshes the persona chip explicitly (a dedicated #statusbar-persona Static,
   updated when PersonaResolved lands — NOT via the existing _status_right()/
   _refresh_status() path, which only manages #statusbar-right's version/tokens).
```

**Timing:** the chip appears after the **first turn's classification** (when the
engine first emits it), exactly like `task_classified` today. On LANDING (before any
prompt) the chip is **absent / neutral placeholder** — engine-truthful: nothing has
reported the id yet. (C2b may seed it eagerly; C2a does not guess.)

**Emit placement:** right after the `task_classified` emit (acp_agent.py:195-196),
BEFORE the gated `persona_load` block (acp_agent.py:198-209) and BEFORE the
clarify/ambiguous early-return (acp_agent.py:219-228, verified). Ordering: classified
→ persona → persona_load → memory_load. The `persona` chip is NOT gated on
`personalized` or `injected` — it fires on every turn-one regardless of dispatch path
(chat/agent/clarify/ambiguous), because the indicator must show for every session.
(Trade-off, accepted: this adds one session_update to the otherwise-minimal clarify
path; the clarify-path tests must be updated to expect it.)

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

harness/tui/state.py        PARSE + EVENT + REDUCE (all here — decision_from_meta and
  the reducer events live in state.py, NOT render.py).
  (a) `persona_from_meta(field_meta) -> str | None` — NEW pure parser mirroring
      `decision_from_meta`: returns harness.persona.id when it's a str, else None.
  (b) `@dataclass(frozen=True) class PersonaResolved: id: str` — NEW reducer event
      (mirrors DecisionOpened at state.py:138).
  (c) `reduce()` (state.py:214-221) gains a PersonaResolved case at the TOP LEVEL
      (not _reduce_agent — that only sees the active agent and cannot change active_id
      or tuple membership). On PersonaResolved(pid): set active_id=pid, and ensure the
      agents tuple has an active AgentSnapshot(id=pid, name=pid) — replace the
      bootstrap "default"/"agent" snapshot's id/name when there's a single agent, or
      add one. Idempotent when pid already == active_id. THIS is the reuse-locked seam
      (comment: C2b reads active_id for highlighting; C2c grows the tuple).
  NOTE: FleetSnapshot exposes `.active` (a @property, state.py:79), NOT active_agent().

harness/tui/app.py          PARSE-CALL + CHIP. Two edits:
  (a) In on_session_update, beside the decision_from_meta call (app.py:777-779), add:
      `pid = persona_from_meta(field_meta); if pid: self._apply(PersonaResolved(pid))`.
      Do NOT route persona through harness_chips (that would append a transcript line).
  (b) Mount a dedicated `#statusbar-persona` Static in the status-bar block
      (app.py:218-226) and update it explicitly when a PersonaResolved lands (e.g. in
      _apply, after reduce, refresh the persona Static from self._snapshot.active).
      Do NOT rely on _status_right()/_refresh_status() — those only manage
      #statusbar-right (version/tokens/commands), per the Codex review.

(No new widget file is required for C2a — a styled Static/persona chip in the status
bar suffices. A reusable persona StatusChip variant can wait for C2b's rail if needed;
C2a does not pre-build it, YAGNI.)
```

**Not touched in C2a:** `AppShell`, `AgentRail`, `SidebarToggle`, `list_personas()`
wiring, switching, engine multiplexing. (C2b/C2c.)

---

## 5. Error handling

| Case | Behavior |
|---|---|
| `state.workspace_dir is None` | id → `"default"`. NOTE (per Codex): in the real `dn-agent` path the default ALWAYS resolves to a concrete workspace, so `workspace_dir is None` is not the normal default case — it occurs only for a `HarnessAgent(workspace_dir=None)` (e.g. tests/mock). The `None → "default"` fallback is correct and harmless; test it with an explicitly-None agent. |
| `persona` chip malformed / no `id` | `persona_from_meta` returns `None` (existing-style `isinstance` guards); no `PersonaResolved` is applied. Chip keeps prior value / placeholder. Never raises. |
| LANDING (before first turn) | chip absent / neutral placeholder — no false claim. |
| repeated `persona` event, same id | `reduce` is idempotent — same `active_id`, no flicker. |
| `session_update` for the chip dropped | best-effort; the chip just doesn't update. No crash. |

**Truth invariant:** the chip equals `workspace_dir.name`, the exact key C1 uses for
the model + persona + memory. The indicator cannot disagree with what the agent runs
as — the reason for an engine push over a TUI echo.

**Scope of "no behavior change" (corrected per Codex):** the persona emit changes NO
model, router, injection, or session-history/transcript-record behavior — `_meta`
session updates are not written to `SessionStore.history` or `SessionState.transcript`
(verified). It is NOT byte-identical on the ACP wire (one extra `_meta` chunk) nor on
the clarify path (one extra update). Because C2a uses the structured
`persona_from_meta → PersonaResolved` path (NOT `harness_chips`), the persona does NOT
appear as a transcript line, and because that path bypasses `render_update`/
`ItemReceived`, it does NOT trigger the empty-meta-chunk→RESPONDING state wart.

---

## 6. Testing strategy

Pure units exhaustively tested; the emit, parse, reduce, and render each isolatable.

- **`tests/test_acp_agent.py`** (extend) — the agent emits a `persona` chip with
  `{id: workspace_dir.name}` once per session, after `task_classified`; emits for
  `default` too (NOT gated on injected); does NOT re-emit on the second turn
  (`persona_emitted` gate); `None` workspace → id `"default"`; fires on the
  clarify/ambiguous path too (unlike persona_load).
- **`tests/test_tui_state.py`** (extend) — `persona_from_meta` returns the id for a
  well-formed chip, `None` for malformed/missing-`id`/non-dict (no raise); `reduce`
  with `PersonaResolved("fred")` sets `active_id="fred"` and makes the active
  `AgentSnapshot` `id/name == "fred"`; idempotent on repeat. (The reuse-locked field —
  comment notes C2b reads `active_id`, C2c grows the tuple.)
- **`tests/test_tui_pilot.py`** (extend) — the status bar's `#statusbar-persona`
  renders the id after a turn; absent / neutral on LANDING.
- **Truth lock** — a test that `persona_from_meta` round-trips `workspace_dir.name`
  through to `FleetSnapshot.active`, and that the emit writes nothing to
  `SessionStore.history` / `SessionState.transcript` (the "no session-history/model
  behavior change" guarantee — NOT a byte-identical-wire claim).

Full suite stays green; net-new tests cover emit + parse + reduce + render.

---

## 7. Definition of done (C2a)

- The running TUI shows the active persona as a status-bar chip, after the first turn.
- The id is sourced from the engine's real resolved `workspace_dir.name`, via
  `FleetSnapshot.active_id` (NOT echoed from `--persona`).
- Default/None → `default`; malformed chips degrade silently; LANDING shows no false
  claim.
- The seam (`persona` _meta emit → `persona_from_meta` → `PersonaResolved` →
  `reduce` → `FleetSnapshot.active_id`) is wired so C2b's rail reads `active_id` and
  C2c's fleet grows the same tuple — with the honest caveat that C2b still adds
  `list_personas()` wiring for the non-active rail entries (see the arc spec).
- Full suite green; parse/event/reduce/emit/render each tested.
