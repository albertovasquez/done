# Persona C2c — in-process persona switching + fleet (design)

**Date:** 2026-06-27 · **Base:** `main` @ `beeccaa` (C2b merged, PR #53) ·
**Worktree:** `.claude/worktrees/persona-c2c` (branch `persona-c2c`).

**Source brief:** `docs/superpowers/specs/2026-06-27-persona-C2c-HANDOFF.md`. This
spec resolves the forks that handoff deferred and is the implementation contract.

---

## 1. What C2c is

C2c is the **last piece of the persona-fleet C2 arc** and the **irreversible engine
fork**. One long-lived `HarnessAgent` process serves **N persona sessions**;
selecting a persona in the rail (or `/persona <id>`) **switches in-process** to that
persona — its own session, memory, and model — **without re-execing**. Each persona's
model resolves at **session-start** from `done.conf[persona]` (single-homed, C1).
Returning to a previously-visited persona resumes its **existing** session.

C2c does NOT add persona *creation* (Phase D) or crons (Phase E). It does NOT add
live concurrent ticking or per-agent state dots (deferred — §7).

## 2. The decisions (locked in brainstorm, 2026-06-27)

Three forks were resolved with the maintainer before any code:

1. **Engine architecture — Option A: in-process N-sessions.** ONE `HarnessAgent`
   process. `SessionStore` holds N sessions, each bound to a persona's workspace; the
   model factory resolves per-session. Switching routes to a loaded session — never a
   process restart. (Rejected: Option B, N subprocesses — heavier client, closer to
   re-exec's failure surface.)
2. **Switch mechanism — `harness/set_persona` ext-method.** A new ACP extension method
   mirroring `harness/set_model` / `harness/set_yolo`. `{id}` → get-or-create that
   persona's session, resolve its model at session-start, return `{session_id, model}`.
   The TUI points `self._session_id` at the returned id. (Rejected: extending
   `new_session` with a persona arg — `new_session` always mints, forcing
   client-side id-per-persona bookkeeping.)
3. **Session reuse — resume existing.** The agent keeps **one durable seat per persona**
   for the process lifetime (a `persona → Seat{session_id, model}` map). Switching back to
   a persona returns the SAME session AND the SAME resolved model (history/transcript/
   memory intact). This is the OpenClaw/Hermes "N stateful seats" model and what "fleet"
   implies. (Rejected: always mint fresh — loses the persona's conversation on
   switch-back.)

**The standard this follows (research, conclusive):** OpenClaw / Hermes / OpenCode /
Codex #12047 all switch agents IN-PROCESS, resolving per-agent model at session-start.
This is exactly why C2b's re-exec switch failed across three Codex passes (per-persona
state leaks). **Re-exec is the wrong primitive. C2c does not re-exec to switch.**

## 3. Architecture & the engine seam

`HarnessAgent` is today a single-persona singleton: `self._workspace_dir` and
`self._store = SessionStore()` are fixed at construction, and `new_session` binds
every session to that one workspace. C2c generalizes the **session**, not the agent —
it stays one process.

**The half-built pipe C2c finishes:** `SessionStore.new(cwd, workspace_dir=None)`
(`acp_session.py:37`) and `SessionState.workspace_dir` (`acp_session.py:27`) ALREADY
accept a per-session workspace (Phase B). The gap is: `new_session` always passes
`self._workspace_dir` (`acp_agent.py:119-121`), and the model resolves once at startup
(`acp_main.py:108-131`), not per-session. C2c closes both.

`self._workspace_dir` stays as the **launch persona** — the first/default seat — not
removed. With no `--persona` and no persona files it resolves to the default workspace
(`persona_select.resolve_workspace(None)` → `default_workspace_dir()`; the agent has
always run with this path, NOT a literal `None`), the one seat is keyed `"default"`, the
model is the engine default, the default templates are inert, and behavior is
**byte-identical** to today (the no-op guarantee). C2c's multiplexing machinery stays
dormant until a second persona is actually selected.

**The active seat is agent-owned state (closes the three model/persist holes Codex
found).** Today the agent is single-persona, so `_worker_model_id` is one process-global
field and `_persona_key()` reads the one launch workspace — there is no notion of "which
seat is active." A naive C2c (resolve model per-session but keep the single field, derive
`_persona_key` from "the active session" that doesn't exist) leaks state. C2c therefore
adds **one piece of agent state: `self._active_persona`** (the persona id of the seat the
client is currently driving), plus **per-seat model storage** so each seat remembers its
own resolved model. Concretely:

- `PersonaSessions` stores, per persona: its `session_id` AND its resolved `model`
  (a `Seat` = `{session_id, model}`). `set_persona(id)` sets `self._active_persona = id`
  (only on success — a failed switch leaves it unchanged).
- `_worker_model_id` is no longer the source of truth. The chat/agent paths read the
  **active seat's** model (`PersonaSessions.model_of(self._active_persona)`), and
  `set_model` writes the **active seat's** model + persists under the active persona.
  This makes "switch to bob, switch back to ana → ana still runs `m-ana`" hold.
- `_persona_key()` returns `self._active_persona` (which is `"default"` at launch).
  No "active session" guesswork; it is explicit agent state. NOT a branch.

**The launch-model crossing must change, or per-seat resolution is dead on arrival.**
Today `tui_main.py` resolves the *launch* persona's `done.conf` model and exports it into
`VIBEPROXY_MODEL` (`tui_main.py:100-105`) so the child's startup ladder picks it up. But
the child cannot tell that exported value from a *real* shell `VIBEPROXY_MODEL`
(`acp_main.py:84` captures `shell_set_model` from `os.environ`), so it wins the rung-1
"real shell env" check. At N=1 that is correct (it IS the launch persona's model). **At
N>1 it is the split-brain Codex flagged:** `VIBEPROXY_MODEL` is process-global, so when
C2c resolves a *different* persona's model it would resolve `ana`/`bob` to the launch
persona's exported model every time, ignoring their own `done.conf`. Fix (spec-level):

- The shell-env rung must mean **only a real, user-exported `VIBEPROXY_MODEL`** — a
  deliberate "force this model for everything." C2c stops `tui_main` from laundering the
  launch persona's persisted model through `VIBEPROXY_MODEL`. The launch persona resolves
  its model the same way every other seat does: through `resolve_session_model(launch_id)`
  reading `done.conf[launch_id]` directly in the child. The TUI still passes the launch
  persona id (`--persona`) and the *real* shell provenance; it no longer pre-resolves and
  re-exports the persisted model.
- Result: a real `VIBEPROXY_MODEL=x` still forces `x` for ALL seats (the documented
  global override). Absent that, each seat gets its own `done.conf[persona].model`. The
  C1 precedence ladder is preserved; the laundering that conflated "persisted launch
  model" with "shell override" is removed. The footer's launch display reads the same
  resolved value (so no behavior change the user sees at N=1).

## 4. Components & data flow

Each unit by responsibility, the seam it touches, and its dependencies.

### Engine side (`harness/acp_agent.py` + a new small module)

- **`PersonaSessions` — the seat map.** A tiny structure owned by `HarnessAgent`,
  mapping `persona_id → Seat`, where `Seat = {session_id, model}` (each seat remembers
  its OWN resolved model — the per-seat storage that fixes the process-global model
  leak). API: `get_or_create(persona_id, *, cwd, store, resolve_ws, resolve_model)
  → Seat`; `model_of(persona_id) → str | None`; `set_model(persona_id, model)`. On miss:
  resolve the workspace (`persona_select.resolve_workspace`), mint a session
  (`store.new(cwd, workspace_dir=ws)`), resolve+store the model (`resolve_model`), record
  the seat. On hit: return the stored seat (the seat-resume invariant — same session AND
  same model). Pure/unit-testable; no I/O of its own beyond the injected callables.
  *Lives in a new module `harness/persona_sessions.py`* (keeps `acp_agent.py` focused).
  - *Depends on:* `SessionStore`, `persona_select.resolve_workspace`, `resolve_session_model`.

- **`resolve_session_model(persona_id, *, shell_set_model) → str | None`.** The C1
  precedence ladder (`acp_main.py:108-131`) extracted into one reusable resolver the
  agent calls when a seat is first created. Same rungs: real shell `VIBEPROXY_MODEL` >
  `done.conf[persona].model` > `.env`-derived `VIBEPROXY_MODEL` > engine default;
  `mock` → `None`. Single-homed in `done.conf [agents.<id>]` — **no second model home.**
  *Lives in `harness/persona_sessions.py` alongside the seat map* (both are "what makes
  a seat"). *Depends on:* `config.load_agent`, `vibeproxy.DEFAULT_MODEL`.
  - **Refactor note:** `acp_main.py`'s startup block is rewritten to call this resolver
    for the launch persona, so there is exactly ONE model-ladder implementation.

- **`ext_method("harness/set_persona", {id})`** — the only new ACP surface. Validates
  `id`, `get_or_create`s the seat, and on success sets `self._active_persona = id`,
  returning `{ok, id, session_id, model}`. On `UnknownPersona`/`InvalidPersonaId` →
  `{ok: false, error, session_id: <active, unchanged>}` and `_active_persona` is left
  unchanged. Mirrors the `set_model` shape exactly (`acp_agent.py:60-109`).

- **`_persona_key()` → the active seat.** Today returns `self._workspace_dir.name` (the
  single persona). Reworked to return `self._active_persona` — explicit agent state set
  by `set_persona` (and `"default"` at launch). The persist sites (`set_model`/
  `set_yolo`) thus persist under the persona the client is actually driving. NOT a
  branch — `"default"` is just the id.

- **The model read sites move to the active seat.** `self._worker_model_id` stops being
  the source of truth: the chat handler (`acp_agent.py:243`) and the agent path read
  `PersonaSessions.model_of(self._active_persona)`; `set_model` writes that seat's model
  (`PersonaSessions.set_model(self._active_persona, m)`) before persisting. This is what
  makes per-seat model actually take effect — resolution alone is not enough.

### Client side (`harness/tui/app.py`)

- **`on_persona_selected(event)`** (app.py:953, today a no-op) — becomes: **guard on
  `self._turn_active`** (NOT `_busy` — `_busy` is only the submit gate + clear/reload;
  the prompt/stream lifecycle is tracked by `_turn_active`, set at app.py:442 and the
  window where a mid-stream `_session_id` repoint would drop late deltas and misdirect
  cancel). If a turn is active, the switch is inert (rail may open; selection no-ops
  until the turn settles). Else: call `ext_method("harness/set_persona", {id})`; on
  `ok`, point `self._session_id` at the returned `session_id`, refresh the footer model
  from the returned `model`, **and `self._apply(PersonaResolved(id))`** so the indicator
  + rail highlight update immediately (see below); on `!ok`, keep the current seat and
  surface a brief notice; close the rail. **One round-trip, no re-exec.**

- **Active-persona update must not depend on the per-session chip.** The engine emits the
  `persona` _meta chip once per session (`state.persona_emitted`, acp_agent.py:202-206),
  so on **switch-BACK** to an already-emitted seat **no chip fires** and the TUI's
  `active_id` would go stale (it updates only at app.py:840-842). Fix: the TUI applies
  `PersonaResolved(id)` directly from the **successful `set_persona` response** (the line
  above). The engine chip remains the per-session truth for a seat's first turn; the
  switch response covers every switch. Both carry the same id, so they agree — no
  conflict, no double-count (the reducer is idempotent on a re-applied active_id).

- **`self._session_id`** stays a single value — repointed on switch. `prompt`/`cancel`
  already read it (app.py:735, 903), so they follow the active seat for free. The
  `_turn_active` guard guarantees the repoint never races an in-flight turn.

### Reducer (`harness/tui/state.py`) — the inherited watch-for (§6)

- **`reduce()` `PersonaResolved`** (state.py:222-235) stops renaming `id` in place.
  Restructured to key the fold on a **stable, immutable `agent_id`**, so N>1 cannot
  produce duplicate ids.

### Data flow — a switch, end to end

```
user clicks "ana" in rail
  → AgentRail posts PersonaSelected("ana")            (shipped C2b widget, unchanged)
  → app.on_persona_selected:
       if self._turn_active: return                    (inert mid-turn — full lifecycle)
       resp = conn.ext_method("harness/set_persona", {"id":"ana"})
          → agent: PersonaSessions.get_or_create("ana")
               miss → resolve_workspace("ana"), store.new(cwd, ws),
                      resolve_session_model("ana") → Seat{session_id, model}
               hit  → return remembered Seat            (seat resumed: session + model)
          → self._active_persona = "ana"               (only on ok)
          → resp = {ok:true, id:"ana", session_id, model:"m-ana"}
       self._session_id = resp["session_id"]           # repoint — no restart
       footer model updated from resp["model"]
       self._apply(PersonaResolved("ana"))             # indicator+rail update NOW
       rail closed                                      #   (no dependence on a chip)
  → next prompt() runs in ana's seat: ana's workspace, memory, AND model
       (agent reads PersonaSessions.model_of("ana"), not a process-global field)
  → first turn of a NEW seat also emits the persona chip (C2a seam, once/session);
     on switch-BACK the chip is suppressed, which is why the response-driven
     PersonaResolved above is the load-bearing update.
```

The active highlight is **truthful on every switch**: it is driven by the successful
`set_persona` response (which the agent only returns after binding the seat), and the
per-session engine chip still confirms a seat's first turn. Both carry the same id.

## 5. Design-system alignment (`components.md` — the approved catalog)

C2c adds **zero new widgets**. The UI is shipped components, wired:

- **`AgentRail`** (`widgets/agent_rail.py`, shipped C2b) is reused **untouched** —
  `set_rows`/`select_id`/`PersonaSelected`/the `●`/`○` glyphs all stay. C2c only makes
  its existing `PersonaSelected` message switch the persona. The catalog still tags
  `AgentRail` `📐 designed-only`; that is **stale** (C2b shipped it). C2c refreshes the
  catalog row to `✅ shipped` (group F + the at-a-glance table).
- **`PersonaIndicator`** (`#statusbar-persona`, C2a, `✅`) remains the active-seat
  anchor — kept engine-truthful via the per-session `persona` _meta chip.
- **Tokens only** (catalog principle #3): the switch's footer model line and any
  "unknown persona" notice use semantic tokens (`$accent`, `$muted`, `$error`) — never
  literal hex.
- **Deferred = catalog-aligned** (§7): per-row `StateDot` (`🟡 built·unwired`) and
  `FleetHeader` counts (`📐 designed-only`) stay deferred. The catalog itself says don't
  wire `StateDot` per row without real per-agent state data — there is none yet.
- **Reuse before invent:** `SelectModal` is the sanctioned picker base, but the rail
  already exists — no new picker.

## 6. The reducer-id watch-for (inherited from C2a — first-class C2c task)

`reduce()`'s `PersonaResolved` case renames the active agent's `id` in place:
`replace(a, id=event.id, name=event.id)` (state.py:227). Correct at N=1, but in a
multi-agent tuple it can produce **two agents sharing one id** (reproduced: agents
`[a,b]`, active `"a"`, + `PersonaResolved("b")` → `['b','b']`).

**Fix:** stop the `PersonaResolved` case from renaming an existing *different* agent
into a duplicate. The case sets `active_id` and, if no agent already carries that id,
seeds one — but the in-place `replace(a, id=event.id, ...)` over a multi-agent tuple is
what creates the collision (`.active` resolves by `id` at state.py:79).

**The right model: `PersonaResolved(id)` means "the agent with this id is now active,"
NOT "rename whoever is active to this id."** When that persona already has a seat in the
tuple (the switch-BACK case), the event must just **point `active_id` at the existing
agent** and preserve its state — it must NOT graft the previously-active agent's tokens/
activity onto it. Only when no agent carries that id does the case seed a fresh one. This
makes the "rename in place" pattern obsolete: switching is selection, not mutation.

The implementation plan chooses the representation (a separate immutable `agent_id` the
fold keys on, or matching on `id` with no in-place rename), but the **binding requirements
are two, and the regression test asserts both:**
1. After any sequence of `PersonaResolved` events, **no two agents share an id**, and
   `active_id` resolves to **exactly one** agent.
2. Switching to a persona that **already has an agent preserves that agent's state**
   (tokens, activity) — it is selected, not overwritten with the prior active agent's
   state. (Codex's counterexample: `[a(tokens=10), b(tokens=99)]` active `a`, then
   `PersonaResolved("b")` must yield active `b` with `tokens=99`, not `tokens=10`.)

## 7. Scope — v1 vs deferred (the maintainer's "reduce scope" steer)

**In v1 (the proven core):**
- In-process switching via `set_persona` (route to a loaded seat, no re-exec).
- Per-session model resolution from `done.conf[persona]` at session-start.
- Persistent per-persona seats (resume on switch-back).
- The reducer-id fix (§6).
- The rail's selection actually switches; `/persona <id>` switches too.

**Deferred (additive on Option A; NOT built in C2c):**
- Live concurrent *ticking* of idle seats (OpenClaw heartbeats) — only the active seat
  runs; others idle until selected.
- Live per-agent **state dots** in the rail (running/idle/scheduled) — the switch works
  without them; no per-agent state data source exists yet.
- True parallel agent loops — **cooperative, one turn at a time** is v1.
- `FleetHeader` counts dropdown — out of scope.

The bar for "done": click a persona → it becomes active in the **same process**, with
its own model + session + memory; switch back → that seat resumes. Live fleet animation
is gravy.

## 8. Error handling

- **Unknown / invalid persona id.** `resolve_workspace` raises `UnknownPersona` (named
  id, no workspace) / `InvalidPersonaId` (charset gate). `set_persona` catches both,
  returns `{ok:false, error, session_id:<active unchanged>}`; the TUI keeps the current
  seat and shows a brief notice. A bad switch never orphans a session or breaks the turn
  (mirrors `set_model`/`set_yolo` `ok` reporting).
- **Switch mid-turn.** `on_persona_selected` is **inert while `self._turn_active`** (set
  for the whole prompt/stream lifecycle at app.py:442 — NOT `_busy`, which only gates
  submit + clear/reload and would leave the streaming window open). A repoint mid-stream
  would drop late deltas (app.py:822 filters by `_session_id`) and misdirect a cancel
  (app.py:901 targets `_session_id`); the `_turn_active` guard closes that window. The
  rail may open; selection no-ops until the turn settles. Matches cooperative-concurrency
  scope. (If a future v2 wants mid-turn switching, it needs per-session delta routing,
  not a single `_session_id` — out of scope here.)
- **Model resolution failure** (e.g. `done.conf` unreadable for that persona). Fall
  through the ladder to the engine default — never `--model ""`. The seat is still
  created; only the model degrades, and `ok` still reports the resolved model so the
  footer is truthful.
- **No-op guarantee** (restated as a boundary). No `--persona` + no persona files →
  exactly one `default` seat (workspace = `default_workspace_dir()`, the path the agent
  has always used — not a literal `None`), engine-default model, inert default templates,
  zero injection, byte-identical to today. `_active_persona` is `"default"` and never
  changes unless a second persona is selected.

## 9. Testing (TDD, per unit)

| Unit | Test |
|---|---|
| `PersonaSessions.get_or_create` | miss mints + stores a `Seat{session_id, model}`; **hit returns the SAME seat** (same session AND same model — resume invariant); distinct personas → distinct ids + independently-resolved models |
| `PersonaSessions.model_of` / `set_model` | per-seat: setting ana's model never changes bob's; `model_of(active)` follows `_active_persona` |
| `resolve_session_model` | `done.conf[ana]` vs `done.conf[default]` resolve independently; missing → engine default; full C1 ladder precedence preserved (incl. `shell_set_model` rung); `mock` → `None` |
| launch-model crossing | with NO real shell `VIBEPROXY_MODEL`, two personas with distinct `done.conf` models each resolve their OWN model (the split-brain regression: launch persona's model must NOT win for the other seat); a REAL shell `VIBEPROXY_MODEL=x` forces `x` for both |
| `set_persona` ext-method | valid id → `{ok, id, session_id, model}` + `_active_persona` updated; unknown → `{ok:false}` + active session AND `_active_persona` unchanged; invalid charset → `{ok:false}`; returned session bound to that persona's workspace |
| `_persona_key` = active seat | `set_model` while ana is active persists under `[agents.ana]`; after switch back to default, `set_model` persists under `[agents.default]` |
| model read site | after switch default→ana→default, a prompt resolves `default`'s model (per-seat storage; NOT ana's, the process-global leak) |
| reducer `PersonaResolved` | **(a)** dup-id repro: `[a,b]` + `PersonaResolved("b")` MUST NOT be `['b','b']`, `active_id` resolves to exactly one agent; **(b)** state-preservation: `[a(tokens=10), b(tokens=99)]` active `a` + `PersonaResolved("b")` → active `b` with `tokens=99`; N=1 unchanged |
| TUI `on_persona_selected` | repoints `_session_id` to the returned id; applies `PersonaResolved(id)` from the response; inert while **`_turn_active`**; `!ok` keeps current seat + indicator |
| switch-back indicator | switch default→ana→default updates the indicator on the way back even though default's seat suppresses the per-session chip (response-driven `PersonaResolved`) |
| no-op | no persona/flag → one default seat (default workspace path), engine default, byte-identical |

**Test-harness reminders (carried from C1/C2):** prompt-driving emit tests live in
`tests/test_acp_session_context.py` (`_FakeConn`/`_ScriptedRouter`/`_build`/`_prompt`),
NOT `test_acp_agent.py` (ext_method only). **Editable-install shadowing**: run worktree
pytest with the WORKTREE as cwd (`.venv/bin/python -m pytest tests/ -q` from the
worktree root); verify a surprising result with
`python -c "import harness.acp_agent as m; print(m.__file__)"`. There is a pre-existing
Textual timing flake (`test_pilot_streams_deltas_into_one_markdown_widget`) unrelated to
persona work.

## 10. Crux tasks flagged for Codex adversarial review

Per the handoff §8 step 4, the engine-multiplexing tasks and the reducer fix get Codex
review (these are exactly where C2b's switch leaked state):
- `PersonaSessions` + `resolve_session_model` (the seat + model multiplexing).
- `set_persona` ext-method + per-session `_persona_key` (the persist-under-active-seat
  correctness).
- The reducer-id fix (§6).

Codex findings are verified against live code before acting (it can sandbox
false-positive).

## 11. Guardrails (load-bearing — do not violate)

- **No re-exec for switching.** In-process routing only.
- **Model single-homed in `done.conf [agents.<id>]`** (C1). Resolve per-session at
  session-start; never add a second model home. `persona.toml` stays non-model
  (`read_skills`, `read_name`).
- **No per-persona code BRANCH.** `default` is just persona #0, threaded through general
  functions. No `if persona_id == "default":` in the routing/model path.
- **The no-op guarantee.** No persona files + no `--persona` → engine-default model,
  zero injection, byte-identical.
- **Persona + memory resolve from `state.workspace_dir`** (Phase B invariant), now
  per-session. Both must agree on the session's workspace.
- **Work in a worktree, never the primary checkout** (AGENTS.md #1). Editable-install
  shadowing (§9).
- **Reuse before invent** (`components.md`): zero new widgets; wire shipped components.

## 12. Definition of done

- One process serves N personas; selecting a persona in the rail (or `/persona <id>`)
  switches **in-process** to it — its own session, memory, **and per-seat model** —
  without re-execing. Switching back resumes that seat (same session AND model).
- The agent owns an explicit `_active_persona`; `_persona_key`, model reads, and
  `set_model`/`set_yolo` persistence all key on it — no "active session" guesswork.
- Per-seat model resolves from `done.conf[persona]` via one shared
  `resolve_session_model` (the launch path uses it too); the launch-model laundering
  through `VIBEPROXY_MODEL` is removed, so per-seat resolution can't be clobbered.
- The reducer fix (§6) holds BOTH invariants: no duplicate ids at N>1, AND switch-back
  preserves the target agent's state.
- The switch updates the indicator/rail via the `set_persona` response (not a per-session
  chip), so switch-BACK highlights correctly.
- The mid-turn guard is `_turn_active` (full prompt/stream lifecycle), not `_busy`.
- No re-exec; no second model home; no per-persona branch; no-op preserved (default
  workspace path, inert templates).
- `components.md` `AgentRail` row refreshed to `✅ shipped`.
- Codex-reviewed spec + plan; PR against `main`; full suite green; shipped.
