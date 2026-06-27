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
3. **Session reuse — resume existing.** The agent keeps **one durable session per
   persona** for the process lifetime (a `persona → session_id` map). Switching back to
   a persona returns the SAME session (history/transcript/memory intact). This is the
   OpenClaw/Hermes "N stateful seats" model and what "fleet" implies. (Rejected: always
   mint fresh — loses the persona's conversation on switch-back.)

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
removed. With no `--persona` and no persona files it is `None`, the one seat is keyed
`"default"`, the model is the engine default, and behavior is **byte-identical** to
today (the no-op guarantee). C2c's multiplexing machinery stays dormant until a second
persona is actually selected.

## 4. Components & data flow

Each unit by responsibility, the seam it touches, and its dependencies.

### Engine side (`harness/acp_agent.py` + a new small module)

- **`PersonaSessions` — the seat map.** A tiny structure owned by `HarnessAgent`:
  `persona_id → session_id`, plus `get_or_create(persona_id, *, cwd, store, resolve_ws)
  → session_id`. On miss: resolve the workspace (`persona_select.resolve_workspace`),
  mint a session (`store.new(cwd, workspace_dir=ws)`), record it. On hit: return the
  stored id (the seat-resume invariant). Pure/unit-testable; no I/O of its own beyond
  the injected `store`/`resolve_ws`. *Lives in a new module
  `harness/persona_sessions.py`* (keeps `acp_agent.py` focused).
  - *Depends on:* `SessionStore`, `persona_select.resolve_workspace`.

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
  `id`, `get_or_create`s the seat, resolves+remembers its model, returns
  `{ok, id, session_id, model}`. On `UnknownPersona`/`InvalidPersonaId` → `{ok: false,
  error, session_id: <active, unchanged>}`. Mirrors the `set_model` shape exactly
  (`acp_agent.py:60-109`).

- **`_persona_key()` → per-session.** Today returns `self._workspace_dir.name` (the
  single persona). The persist sites (`set_model`/`set_yolo`) must persist under the
  **active session's** persona, so this is reworked to derive from the active session's
  `workspace_dir.name` (falling back to `"default"`). NOT a branch — `"default"` is
  just the id.

### Client side (`harness/tui/app.py`)

- **`on_persona_selected(event)`** (app.py:953, today a no-op) — becomes: guard on
  `self._busy` (inert mid-turn, like `action_reload`); else call
  `ext_method("harness/set_persona", {id})`; on `ok`, point `self._session_id` at the
  returned `session_id` and refresh the footer model from the returned `model`; on
  `!ok`, keep the current seat and surface a brief notice; close the rail. **One
  round-trip, no re-exec.**

- **`self._session_id`** stays a single value — repointed on switch. `prompt`/`cancel`
  already read it (app.py:735, 903), so they follow the active seat for free.

### Reducer (`harness/tui/state.py`) — the inherited watch-for (§6)

- **`reduce()` `PersonaResolved`** (state.py:222-235) stops renaming `id` in place.
  Restructured to key the fold on a **stable, immutable `agent_id`**, so N>1 cannot
  produce duplicate ids.

### Data flow — a switch, end to end

```
user clicks "ana" in rail
  → AgentRail posts PersonaSelected("ana")            (shipped C2b widget, unchanged)
  → app.on_persona_selected:
       if self._busy: return                          (inert mid-turn)
       resp = conn.ext_method("harness/set_persona", {"id":"ana"})
          → agent: PersonaSessions.get_or_create("ana")
               miss → resolve_workspace("ana"), store.new(cwd, ws),
                      resolve_session_model("ana")
               hit  → return remembered session_id     (seat resumed)
          → resp = {ok:true, id:"ana", session_id, model:"..."}
       self._session_id = resp["session_id"]           # repoint — no restart
       footer model updated from resp["model"]
       rail closed
  → next prompt() runs in ana's session: ana's workspace, memory, model
  → engine emits persona _meta chip "ana" (C2a seam, per session)
       → persona_from_meta → PersonaResolved("ana")
       → reduce sets FleetSnapshot.active_id="ana" → PersonaIndicator + rail highlight
```

The active highlight stays **engine-truthful**: it reflects the seat that actually
served the turn (the `persona` _meta chip), not merely what the client clicked.

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
what creates the collision. The implementation plan chooses the mechanism — either
(a) a separate immutable `agent_id` the fold matches on (so display `id` can change
without colliding identity), or (b) de-dup after the remap — but the **binding
requirement is invariant, not mechanism:** *after any sequence of `PersonaResolved`
events, no two agents share an id, and `active_id` always resolves to exactly one
agent.* Covered by the dup-id regression test (§9), which asserts the invariant
directly so either mechanism passes.

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
- **Switch mid-turn.** `on_persona_selected` is **inert while `self._busy`** (the guard
  `action_reload` already uses). The rail may open; selection no-ops until the turn
  settles. Matches cooperative-concurrency scope.
- **Model resolution failure** (e.g. `done.conf` unreadable for that persona). Fall
  through the ladder to the engine default — never `--model ""`. The seat is still
  created; only the model degrades, and `ok` still reports the resolved model so the
  footer is truthful.
- **No-op guarantee** (restated as a boundary). No `--persona` + no persona files →
  exactly one `default` seat, engine-default model, zero injection, byte-identical to
  today.

## 9. Testing (TDD, per unit)

| Unit | Test |
|---|---|
| `PersonaSessions.get_or_create` | miss mints + stores; **hit returns the SAME id** (seat-resume invariant); distinct personas → distinct ids |
| `resolve_session_model` | `done.conf[ana]` vs `done.conf[default]` resolve independently; missing → engine default; full C1 ladder precedence preserved (incl. `shell_set_model` rung); `mock` → `None` |
| `set_persona` ext-method | valid id → `{ok, id, session_id, model}`; unknown → `{ok:false}` + active session unchanged; invalid charset → `{ok:false}`; the returned session is bound to that persona's workspace |
| `_persona_key` per-session | `set_model` while ana's seat is active persists under `[agents.ana]`, not the launch persona |
| reducer `PersonaResolved` | the **dup-id repro** (`[a,b]` + `PersonaResolved("b")` MUST NOT be `['b','b']`); `active_id` always resolves to exactly one agent; N=1 unchanged |
| TUI `on_persona_selected` | repoints `_session_id` to the returned id; inert while `_busy`; `!ok` keeps current seat |
| no-op | no persona/flag → one default seat, engine default, byte-identical |

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
  switches **in-process** to it — its own session, memory, model — without re-execing.
  Switching back resumes that seat.
- Per-session model resolves from `done.conf[persona]` at session-start; one model-ladder
  implementation (shared with the launch path).
- The reducer-id watch-for (§6) is fixed: no duplicate ids at N>1.
- No re-exec; no second model home; no per-persona branch; no-op preserved.
- `components.md` `AgentRail` row refreshed to `✅ shipped`.
- Codex-reviewed spec + plan; PR against `main`; full suite green; shipped.
