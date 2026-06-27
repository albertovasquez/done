# Persona Phase C1 — selection & isolation core (engine, headless)

**Status:** design / spec (no implementation in this doc)
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Tracker:** GitHub issue #29 (Multi-agent Phase C). This spec covers **C1** only —
the headless engine core. The TUI fleet shell (AppShell + AgentRail drawer) is a
separate **C2** spec built on top of C1's data.
**Roadmap:** `docs/superpowers/specs/2026-06-26-persona-fleet-design.md`.
**Builds on:** Phase A (persona injection contract, PR #20), templates (PR #27),
Phase B (memory + per-session `workspace_dir` pipe, PR #37).

---

## 1. Purpose

DoneDone becomes **multi**-persona: more than one workspace, and the ability to
select one explicitly. C1 delivers, all verifiable headless (pytest, no TUI):

1. **Selection** — a `--persona <id>` flag picks which workspace the agent runs
   as; the `default` persona when unset.
2. **Isolation** — each persona's sessions and memory are physically its own
   directory tree; persona A never sees persona B's history.
3. **Per-persona model** — each persona remembers its own model in
   `done.conf [agents.<id>]`, with a defined, branch-free precedence ladder.

This is the "valve" on Phase B's "pipe": B gave every session a
`workspace_dir`; C1 makes *which* workspace selectable.

**Out of scope (deliberately):** the TUI AgentRail/AppShell drawer (C2);
persona *creation* / onboarding (Phase D); live mid-session persona switching
(between-sessions only, matching first-turn-only injection); content-based
auto-routing to a persona (D5, rejected).

---

## 2. The load-bearing rules this spec must honor

These come from the roadmap and the Phase-A/B reviews; every design choice below
serves them.

- **D3 — no per-persona code branch, ever.** `default` is just persona #0. There
  is no `persona_id == "default"` special-case anywhere in the
  selection / precedence / injection path. (The `default`-named config functions
  become thin `id="default"` wrappers — not a branch.)
- **Single chokepoint.** Selection resolves a `workspace_dir` once, at boot;
  everything downstream runs the *same* `compose_context()` injection path A/B
  already proved. C1 changes only *which* workspace is selected.
- **No-op guarantee.** No `--persona`, no `done.conf`, no persona files →
  engine-default model, zero persona/memory injection → byte-identical to pre-C1.
- **Single-home model.** The worker model lives in exactly one place
  (`done.conf [agents.<id>]`). No second writer, no clobber.
- **Best-effort config.** A missing/corrupt `done.conf` or `persona.toml` never
  crashes boot; reads degrade to defaults.

---

## 3. The real boot architecture (three entrypoints)

C1's selection seam must cross a **process boundary** and reach **three**
independent entrypoints. (An earlier draft missed this; an adversarial review
corrected it against live code — see §9.)

```
tui_main.main()                         [PARENT process — the TUI]
  ├─ _resolve_model(persona_id) → done.conf [agents.<id>].model
  │     → export VIBEPROXY_MODEL  (subject to shell-vs-.env precedence, §5)
  ├─ agent_cmd = [python -m harness.acp_main, --model, --cwd, --persona <id>, (--yolo)]
  └─ spawns ↓
       acp_main._main()                 [CHILD process — the ACP agent]
         ├─ resolve_workspace(args.persona)   → workspace_dir  (raise → clean exit)
         ├─ worker_model_id ← VIBEPROXY_MODEL  (set by the parent)
         └─ HarnessAgent(workspace_dir=<selected>)   # was: hardcoded default

run_traced._main()                      [STANDALONE dev path — third reader]
  ├─ resolve_workspace(args.persona)    → workspace_dir
  ├─ worker_model_id ← per-persona resolve
  └─ resolve_persona/memory(workspace_dir)   # was: hardcoded default
```

**Why the model resolves in `tui_main` (parent), not `acp_main` (child):** the
model already crosses the boundary via the `VIBEPROXY_MODEL` env var
(`tui_main` exports it; `acp_main` reads it via `vibeproxy.default_model()`).
C1 keeps that channel — it only changes *which* `done.conf` key the parent reads.
The child resolves the *workspace*, because that's what it constructs the agent
with. `run_traced` is standalone (no parent) so it does both itself.

---

## 4. Model home & the precedence ladder

### 4.1 Single home: `done.conf [agents.<persona-id>]`

The model is per-persona runtime state and lives with the rest of the
per-persona runtime state in `done.conf`. `config.py` already round-trips
uuid-keyed `[agents.<key>]` tables; C1 wires reads/writes to the *selected* key.

- `done.conf [agents.default].model` already holds today's default model — so
  **existing installs are byte-identical and need NO migration.**
- A named persona `fred` reads/writes `done.conf [agents.fred].model`.
- Both `set_model` and `set_yolo`'s pin keep writing `done.conf` via the existing
  merge-safe `update_default` — **one file, one writer mechanism, no dual-home.**

`persona.toml` (per-workspace) is introduced for **non-model static config
only** — extra skill dirs (D4). The model is never written there. (Keeping the
model out of `persona.toml` is what prevents the dual-home clobber bug the
review surfaced.)

```toml
# ~/.config/harness/agents/<id>/persona.toml   (optional, human-authored)
skills = ["~/some/extra/skills"]      # extra skill roots; falls back to skills_dirs()
```

### 4.2 The precedence ladder (resolve the worker model at boot)

Highest wins. Keyed by the selected persona id throughout.

1. **real shell `VIBEPROXY_MODEL`** — a model id the user exported in their
   shell; per-launch, never persisted. (Note: `--model` is the *backend* choice
   `mock`/`vibeproxy`, **not** a model-id override — there is no model-id CLI flag
   today, and C1 does not add one. The only per-launch model-id channel is this
   env var.)
2. **`done.conf [agents.<persona>].model`** — the persisted per-persona model;
   where `set_model` / `set_yolo` write.
3. **`.env`-derived `VIBEPROXY_MODEL`** — a project `.env` value.
4. **engine default** (`vibeproxy.DEFAULT_MODEL`).

Rungs 1 and 3 are *both* `VIBEPROXY_MODEL` but at different precedence: a value
the **shell** exported outranks `done.conf`, while a `.env`-derived value does
not. This distinction exists in the live code and is test-locked
(`test_tui_main.py` `test_persisted_model_beats_dotenv`,
`test_real_shell_env_beats_persisted_model`); C1 preserves it, keyed per persona.
The mechanism is unchanged — `tui_main` captures `shell_set_model =
"VIBEPROXY_MODEL" in os.environ` *before* `load_env`, then overwrites with the
persisted model only when the shell did not set it.

### 4.3 Live `/models` swap

`ext_method("harness/set_model")` writes the model into **this agent's own**
persona key — derived as `self._workspace_dir.name` (the directory basename *is*
the persona id; no new state). It updates `done.conf [agents.<own-id>]`, leaving
every other persona's table untouched. `set_yolo`'s pin targets the same key.
Next launch of *that* persona reads it back via rung 2; a different persona reads
its own table. No cross-persona clobber.

`set_model` returns the **real persisted state** (mirroring `set_yolo`), so a
failed per-persona write is visible to the client rather than masked by an
unconditional `{"ok": True}`.

---

## 5. Components (files & responsibilities)

Each unit has one purpose, a defined interface, and a headless test.

### New files

```
harness/persona_select.py    NEW — the resolver (the one selection chokepoint)
  resolve_workspace(persona_id: str | None) -> Path
      None / "default"  -> paths.default_workspace_dir()
      "<id>"            -> paths.config_dir()/"agents"/<id>   if it exists
      missing dir       -> raise UnknownPersona(persona_id)
  list_personas() -> list[PersonaInfo]    # read-only enumerate (id, has-model);
                                          # for /persona + C2's rail
  UnknownPersona(Exception)
  # NOTE: no migration function — none needed (model never moved).

harness/model_resolve.py     NEW — the precedence ladder (the Codex-review unit)
  resolve_model(*, shell_env: str | None, dotenv: str | None,
                persisted: str | None, engine_default: str) -> str
  # pure function of its inputs (no global reads inside) — exhaustively testable.

harness/persona_config.py    NEW — persona.toml reader (NON-model config only)
  read_skills(workspace_dir: Path | None) -> list[Path]   # [] when unset/unreadable
  # scoped to ONE workspace dir; stdlib tomllib; never raises into boot.
```

### Modified files

```
harness/config.py            persona-keyed config API. Generalize the default-only
  save_agent(persona_id, *, backend, model, yolo_pinned=None) -> None
  load_agent(persona_id) -> AgentConfig | None
  yolo_pinned(persona_id="default") -> bool
  # save_default/load_default become thin wrappers: save_agent("default", ...),
  # load_agent("default"). NOT a branch — "default" is just the passed id.
  # update_default's merge-safe / refuse-empty logic generalizes to update_agent.

harness/acp_main.py          add --persona; resolve_workspace(args.persona)
  (UnknownPersona -> stderr + non-zero exit, NEVER falls back to default);
  pass the resolved workspace_dir to HarnessAgent (was hardcoded
  paths.default_workspace_dir()).

harness/tui_main.py          add --persona; _resolve_model(persona_id) reads the
  per-persona done.conf key; export VIBEPROXY_MODEL (shell-vs-.env logic intact);
  append --persona <id> to agent_cmd; carry it through _relaunch_args so /reload
  preserves the persona.

harness/acp_agent.py         set_model writes config.save_agent(
  self._workspace_dir.name, ...) and returns the real persisted state; set_yolo's
  pin targets the same key. new_session unchanged (records self._workspace_dir).

harness/run_traced.py        add --persona; resolve workspace + per-persona model
  identically, so the dev path is not pinned to default.

harness/persona.py / memory.py / acp_session.py   UNCHANGED — they already
  resolve from the workspace_dir handed to them; C1 just hands a selected one.
```

### The `/persona` command (keyboard path)

A thin command that calls `list_personas()` and selects an id (re-launch / new
session against the chosen workspace), reusing the existing command/SelectModal
plumbing. Minimal in C1 — full click-to-switch lives in the **C2** AgentRail.

---

## 6. Isolation

One running process = one persona (the chosen isolation key). The agent launched
for `fred` records `workspace_dir = …/agents/fred` on every `new_session` (the
Phase-B pipe, unchanged). `resolve_persona(ws)` / `resolve_memory(ws)` read
fred's tree; `default` in a separate process reads `…/agents/default`. They never
share a `SessionStore` because one process serves one persona. fred's
history/memory is physically a different directory tree from default's.

**Known C2 blocker (flagged, not solved here):** `new_session` records the
agent's single `self._workspace_dir` and takes no per-session workspace argument.
That is correct and sufficient for C1 (one process = one persona) but is the seam
C2 must open to multiplex personas in one TUI process. C1 does **not** claim
multi-persona-in-one-process; it is explicitly C2's job.

---

## 7. Error handling

| Failure | Behavior |
|---|---|
| Unknown `--persona` id | Hard fail at boot in `acp_main` / `run_traced`: stderr message, non-zero exit. **Never** falls back to default. |
| `done.conf` missing / corrupt | `load_agent → None` → ladder falls to engine default. Never raises. (Same contract as today's `load`.) |
| `persona.toml` missing / corrupt | `read_skills → []`. Never raises. |
| `set_model` write fails | Returned state reflects the real (unchanged) persisted value; the in-session swap still applies; next launch won't see it. |
| `update_agent` would create a table with empty backend/model | No-ops (existing refuse-empty rule), so a flagless relaunch never resolves `--model ""`. Generalized from the default to any id. |
| No `--persona`, no config, no persona files | Engine-default model, zero injection — byte-identical to pre-C1. |

---

## 8. Testing strategy

Pure units exhaustively tested headless; the ladder is its own isolatable
Codex-review target; the no-op and back-compat guarantees each get a regression
lock. Full suite (393 today) stays green.

- **`tests/test_persona_select.py`** (NEW) — `resolve_workspace(None|"default")`
  → default dir (proving `default` isn't special-cased); `("fred")` present →
  fred dir; `("nope")` absent → raises `UnknownPersona`; `list_personas()`
  read-only (asserts no dir created).
- **`tests/test_model_resolve.py`** (NEW) — one test per rung, then
  precedence-between-rungs (shell > done.conf > .env > engine default). Pure
  inputs. This file is the ladder's correctness proof.
- **`tests/test_config.py`** (EXTEND) — `save_agent("fred")` / `load_agent("fred")`
  round-trip; writing `fred` never touches `[agents.default]`; `yolo_pinned("fred")`
  independent of `yolo_pinned("default")`. Retarget
  `test_round_trip_set_model_then_resolve` through the persona-keyed API
  (contract preserved); confirm `save_default`/`load_default` still pass as
  `id="default"` wrappers.
- **`tests/test_acp_agent.py`** (EXTEND) — rewrite the two set_model persistence
  tests to assert under the agent's own persona key (intentional retarget, not a
  silent break); new: agent on `…/agents/fred` writes `[agents.fred]`, leaves
  `[agents.default]` untouched; `set_model` returns real persisted state on a
  simulated write failure.
- **`tests/test_tui_main.py`** (EXTEND) — `--persona fred` reaches `agent_cmd`
  and survives `_relaunch_args`; `_resolve_model("fred")` reads `[agents.fred]`;
  **keep green** the existing shell-vs-.env precedence tests (now persona-keyed).
- **`tests/test_acp_main.py`** (EXTEND/NEW) — `--persona fred` (dir exists) →
  agent constructed with fred's workspace; `--persona nope` → non-zero exit, no
  agent; no `--persona` → default dir (byte-identical).
- **`tests/test_run_traced.py`** (EXTEND) — `--persona fred` resolves fred's
  workspace + model; absent → default.
- **Regression locks:** (1) **No-op** — no persona/config/flags → engine default,
  zero injection, byte-identical (extends the seeded-default no-op test).
  (2) **Back-compat** — pre-C1 `done.conf [agents.default].model = X` → after C1
  boot with no `--persona`, default still resolves X. (Trivially true since the
  model never moved; the test makes the guarantee explicit.)

---

## 9. Design history (adversarial review)

An earlier draft of this spec made the model's live-swap home `persona.toml`,
with a one-time `done.conf → persona.toml` migration for the default. An
adversarial design review (doubt-driven, against live code) found that fatal and
it was redesigned:

- **Wrong boot architecture.** The draft claimed `tui_main` constructs the agent.
  It does not — `tui_main` spawns `acp_main` as a subprocess and the model
  crosses via `VIBEPROXY_MODEL`. Corrected in §3.
- **Forgotten third reader.** `run_traced` independently reads the model and the
  default workspace. Now a first-class entrypoint in §3/§5.
- **Dual-home clobber.** `set_yolo` *also* writes `model` into `done.conf`; moving
  `set_model` to `persona.toml` would leave the model in two files that can
  disagree — the exact bug Phase C exists to prevent. Resolved by single-homing
  the model in `done.conf [agents.<id>]` (§4), which also **eliminates the
  migration entirely** and keeps the `set_model` / round-trip tests valid.
- **Overclaimed D3.** A `default`-keyed migration would have been the very
  `default` special-case D3 forbids. With no migration, there is none.
- **Shell-vs-.env precedence.** The ladder preserves the test-locked distinction
  (§4.2), keyed per persona.
- **Honest C2 boundary.** `new_session` taking no per-session workspace is flagged
  as a C2 blocker (§6), not papered over as solved.

---

## 10. Definition of done (C1)

- `--persona <id>` selects an existing workspace across all three entrypoints;
  `default` when unset; an unknown id is a clean hard error.
- Sessions and memory are physically isolated per persona.
- The model is single-homed in `done.conf [agents.<id>]` with the tested
  precedence ladder; live `/models` swaps persist per persona; existing installs
  see no model reset (no migration needed).
- No per-persona code branch in the selection / precedence / injection path.
- Full suite green; net-new tests cover every new unit; every retargeted test is
  intentional and asserted.
- (C2 — the AppShell + AgentRail drawer — is a separate spec built on this data.)
