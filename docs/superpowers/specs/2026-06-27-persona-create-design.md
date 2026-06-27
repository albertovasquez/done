# Persona creation — TUI modal (design)

**Date:** 2026-06-27 · **Base:** `main` @ `4ef59f8` (C2c merged, PR #56) ·
**Worktree:** `.claude/worktrees/persona-create` (branch `persona-create`).

This is **Phase D** (persona creation), deferred from C1 (`persona_select.py:6`:
"Creation of new workspaces is out of scope (Phase D)"). It builds directly on C2c
(in-process switching, just merged).

---

## 1. What this is

A TUI flow to **create a new persona by name**: press `n` in the persona rail → a
modal takes a name → the workspace is created (inert template trio) → a loading
animation plays → the new persona becomes **active** and the rail highlights it.
No CLI command. Reuses the C2c switch machinery + the existing modal/spinner
components.

## 2. The decisions (locked in brainstorm, 2026-06-27)

1. **Create + switch** — creating a persona immediately makes it active (chains into
   C2c's `set_persona` seat activation) and lands on the rail with it highlighted.
2. **Entry point = `n` in the rail** — matches the C2b rail mockup footer
   (`⇑⇓ select · ⏎ switch · n new`). The rail is the one "manage personas" surface.
3. **Seed content = the inert template trio** — copy the same bundled
   SOUL/IDENTITY/USER.md templates `default` gets (generalize `seed_default_workspace`),
   so the new persona is a valid no-op until edited.

## 3. Architecture & the engine seam

Persona creation is a thin path that reuses existing machinery. Three layers:

**Engine — `harness/persona.py`:**
- **`_copy_persona_templates(dest: Path) -> None`** (NEW private helper) — the ONLY
  shared logic: `mkdir(parents=True, exist_ok=True)` then copy each `PERSONA_FILES`
  entry from `bundled_persona_templates_dir()` into `dest`, **byte-for-byte**
  (`read_bytes`/`write_bytes`), skipping any file that already exists. No validation, no
  raise-policy of its own — callers own those. (Codex 1A/1B/5B: seed must NOT route
  through the validating `create_persona`; the byte-copy makes "byte-identical" provable.)
- **`create_persona(persona_id: str) -> Path`** — the new public core. Validates the id
  (reuse `persona_select._VALID_ID` `^[a-z0-9_-]+$` + **explicitly reject** `RESERVED_KEY`
  "default" — `_VALID_ID` alone allows "default", so the reserved check is separate),
  refuses if the target **`.exists()`** (file OR dir — `PersonaExists`, not just `is_dir`;
  Codex 3C), then calls `_copy_persona_templates(target)`. Returns the new workspace
  `Path`. Raises `InvalidPersonaId` (bad charset or "default"), `PersonaExists` (target
  already present), or lets `OSError` propagate (read-only home, etc. — explicit creation
  REPORTS).
- **`seed_default_workspace()`** — refactored: **no-op if the default dir exists**
  (returns immediately — does NOT backfill an existing dir; Codex 1B), else
  `_copy_persona_templates(default_workspace_dir())` wrapped in its existing
  try/except-OSError-pass (never raises — startup contract). It does NOT call
  `create_persona` (which would reject "default").

**ACP ext-method — `harness/acp_agent.py`:**
- **`_activate_seat(id) -> dict`** (NEW private method, extracted from the current
  `set_persona` body) — get-or-create the seat, set `_active_persona = id`, mirror
  `_worker_model_id`, stamp the session's `worker_model`, return `{ok:true, id,
  session_id, model}`. `set_persona`'s ext-method branch becomes a thin wrapper
  (validate id → `_activate_seat`). ONE activation path.
- **`harness/create_persona`** (mirrors `set_persona`) — wraps **both** create and
  activation in one try: `persona.create_persona(id)` then `_activate_seat(id)`. On ANY
  of `InvalidPersonaId`/`PersonaExists`/`OSError`/`UnknownPersona` → `{ok:false, error}`
  with **`_active_persona` unchanged** (the `_active_persona =` assignment lives inside
  `_activate_seat`, which only runs after a successful create; if activation itself
  raises before that assignment, active stays unchanged). A just-created dir orphaned by
  a later activation failure is acceptable (no rollback — §5). (Codex 2A.)

**TUI — `harness/tui/widgets/new_persona_modal.py` (NEW) + `harness/tui/app.py`:**
- **`NewPersonaModal(ModalScreen)`** — an `Input` for the name + a status line. States:
  **input** → **creating** (spinner `◐◓◑◒` via `set_interval`, reusing the
  `ActivityStatus` cycle + reduced-motion static `◐`) → closes on success / **error**
  (inline message, back to input). Returns the created id (or None) via the push_screen
  callback.
- **Rail `n` key** opens the modal; **`_on_created(id)`** runs the create ext-method and,
  on success, repoints `_session_id` + applies `PersonaResolved(id)` + refreshes the
  rail — **the same success path as `on_persona_selected`** (factor the shared bit into
  one helper both call).

**Why this shape:** zero new *concepts*. Generalize the seeder + one ext-method that
chains into C2c's switch + one modal that reuses the spinner. Engine stays single-homed
and branch-free (`default` is the one id you canNOT create).

## 4. Components & data flow

### Engine (`harness/persona.py`)
- `create_persona(persona_id) -> Path` — validate / no-clobber / mkdir / copy trio.
  *Depends on:* `paths`, `persona_select` (`_VALID_ID`, `RESERVED_KEY`).
- `seed_default_workspace()` — delegates the copy to a shared helper; startup contract
  unchanged.
- NEW `class PersonaExists(Exception)` — the workspace already exists (opposite of
  `UnknownPersona`).

### ACP ext-method (`harness/acp_agent.py`)
- `ext_method("harness/create_persona", {id})` → `create_persona(id)` → chain into the
  shared `_activate_seat(id)` (the existing set_persona body, extracted) → `{ok, id,
  session_id, model}`. Reuses C2c's seat machinery; no new switching logic.

### TUI (`harness/tui/widgets/new_persona_modal.py` NEW)
- `NewPersonaModal` — input + spinner + error line. Tokens only (`$accent`/`$muted`/
  `$error`). Reuses the modal base + the `◐◓◑◒` cycle.

### TUI app (`harness/tui/app.py`)
- Rail `n` → `push_screen(NewPersonaModal(), _on_created)`.
- `_on_created(id)` → `ext_method("harness/create_persona", {id})`; on ok, the shared
  switch-success helper (repoint `_session_id`, `_apply(PersonaResolved(id))`,
  `_refresh_persona`, `_refresh_meta_line`, refresh + close rail).

### Data flow (create, end to end)
```
rail open → press 'n'
  → push_screen(NewPersonaModal)
  → user types "fred" → Enter (empty name ignored)
  → modal: state=creating, spinner ◐◓◑◒ ticking
  → app: resp = ext_method("harness/create_persona", {"id":"fred"})
       → engine: persona.create_persona("fred")        # mkdir + copy templates
            invalid/"default" → InvalidPersonaId → {ok:false}
            dir exists        → PersonaExists    → {ok:false}
            OSError           → {ok:false}
       → on success: _activate_seat("fred")  (C2c path: get_or_create + activate)
       → {ok:true, id:"fred", session_id, model}
  → on ok: modal closes; _session_id repointed; _apply(PersonaResolved("fred"));
       rail refreshed → lists fred + highlights it active
  → on !ok: modal shows error inline ($error), stays open, name field refocused
```
The success path **is** C2c's switch path; creation prepends "make the workspace first."

## 5. Error handling

- **Invalid name** (charset / reserved "default") → `InvalidPersonaId` → `{ok:false}`;
  modal shows the message ($error), stays open, name refocused. No half-created dir.
- **Already exists** → `PersonaExists` → `{ok:false, error}`; modal stays open.
- **Empty/whitespace name** → modal Enter handler ignores it (no ext-method call).
- **Filesystem failure** (read-only home) → `create_persona` lets `OSError` propagate;
  ext-method catches → `{ok:false, error}`. Unlike `seed_default_workspace` (swallows to
  protect startup), explicit creation REPORTS the failure — the user asked for it.
- **Partial create** (mkdir ok, a copy fails) → no rollback (YAGNI). An empty/partial
  workspace is still a valid no-op persona; a retry hits `PersonaExists` cleanly.

## 6. Rail-key interaction (the one UI subtlety)

The rail is a `ListView`; `n` must open the modal WITHOUT the list consuming it as
navigation. `n` gets an explicit rail binding that posts a "new persona" message the
app catches (mirrors `PersonaSelected`). The modal is a `ModalScreen` (push_screen),
overlays cleanly, `esc` cancels back to the rail. While the create ext-method runs the
modal owns focus — no rail race (modal-lifecycle-scoped, analogous to C2c's
`_turn_active` guard).

## 7. Design-system alignment (`components.md`)

- Spinner = the catalog's single looping `◐` animation (`ActivityStatus`/`ActivityGlyph`
  cycle) — reused, reduced-motion fallback honored.
- Modal extends the existing modal base (sibling of `SelectModal`/`PermissionModal`).
- Tokens only (`$accent` field, `$muted` hint, `$error` message). Rail footer shows
  `n new` (already in the C2b mockup).
- ONE new widget (`NewPersonaModal`) — justified: no existing modal takes a free-text
  *create* input with a create-then-switch lifecycle. Add a catalog entry with rationale.

## 8. Testing (TDD, per unit)

| Unit | Test |
|---|---|
| `_copy_persona_templates` | copies the trio byte-for-byte (assert `read_bytes()` equals the bundled template bytes); skips a file that already exists; creates the dir |
| `create_persona` | creates dir + copies the trio; **rejects "default"** (`InvalidPersonaId`); rejects bad charset (`fred.smith`, `Fred`, spaces); **rejects existing dir AND existing file** at the target (`PersonaExists`); returns the workspace path |
| `seed_default_workspace` refactor | no-op when default exists (does NOT backfill missing files into an existing default dir — regression of the current contract); never raises (read-only home); default still seeded on first run with byte-identical inert templates |
| `create_persona` ext-method | valid → `{ok, id, session_id, model}` + `_active_persona == id` + the workspace exists on disk; dup id → `{ok:false}` (active unchanged); invalid id → `{ok:false}`; missing id → `{ok:false}` |
| `NewPersonaModal` | Enter with a name → posts the id; empty/whitespace name → ignored (no post); error state → message shown + modal stays open; cancel/esc → posts None |
| app `n`-opens + `_on_created` | `n` in rail opens the modal; successful create repoints `_session_id` + applies `PersonaResolved` + rail lists+highlights the new id; failed create keeps current persona + shows notice |
| no-op | creating a persona does not touch `default`'s files; the no-op guarantee for a no-persona launch is unaffected |

**Test-harness reminders:** persona-on-disk fixtures use `tmp_path/agents/<id>` +
`XDG_CONFIG_HOME` (see `tests/test_acp_agent.py` `isolated_config`). ext_methods via
`asyncio.run`. Editable-install shadowing — run worktree pytest with the WORKTREE as cwd
(`.venv/bin/python -m pytest tests/ -q` from the worktree root).

## 9. Crux tasks for Codex adversarial review

- The `create_persona` ext-method — it chains into C2c's seat activation (the surface
  Codex scrutinized for cross-persona leaks); verify create-then-activate doesn't leak or
  half-activate on a failed create.
- The `seed_default_workspace` refactor — must NOT break the startup no-op / never-raise
  contract (the byte-identical default-seed behavior).

Codex findings verified against live code before acting (it can sandbox false-positive).

## 10. Guardrails (load-bearing)

- **Reuse before invent** (`components.md`): one new widget (justified); reuse the modal
  base + the spinner + the C2c switch path.
- **No second model home / no per-persona branch** (C1/C2c): creation activates via the
  same single-homed seat path; `default` is just the one reserved id.
- **The no-op guarantee** (A/B/C): a no-persona launch is unchanged; the default-seed
  startup behavior is byte-identical after the refactor.
- **Create is explicit-and-reported** (vs. seed-is-silent): `create_persona` surfaces
  failures; `seed_default_workspace` keeps swallowing to protect startup.
- **Work in the worktree** (AGENTS.md #1); editable-install shadowing (§8).

## 11. Definition of done

- Pressing `n` in the rail opens a name modal; entering a valid name creates the
  workspace (inert trio), plays the spinner, switches to it, and highlights it in the
  rail — without re-exec.
- Invalid / duplicate / FS-failure names show an inline error and keep the modal open.
- `seed_default_workspace` still seeds `default` on first run, never raises, no-op if
  present (regression green).
- One new widget, catalog entry added; tokens only; spinner reused.
- Codex-reviewed; full suite green; PR against `main`; shipped.
