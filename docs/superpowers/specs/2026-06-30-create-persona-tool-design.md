# Design — `create_persona` agent tool

**Date:** 2026-06-30
**Status:** Approved (brainstorming) → implementing
**Branch:** `create-persona-tool`

## Problem

A user can type "create a persona named Robbie" but nothing happens: the router
classifies it as `chat_question` (there is no persona-create task type), and the
`chat_question`/agent path has no tool that can actually create a persona.
Persona creation is reachable **only** via the TUI (`NewPersonaModal`, key `n` →
`ext_method("harness/create_persona")`). Natural-language creation is a dead end.

## Decision

Add a `create_persona` **agent tool** that reuses the existing
`persona.create_persona` backend. No router change. This mirrors `CreateJobTool`
(PR #159): the same pattern that turned a UI-only privileged operation into an
agent-reachable one.

Rejected alternatives:
- **Router category (`persona_create`)** — a new privileged path through the
  router LLM, which can misfire. More surface, more risk.
- **UI-only + guide** — agent just tells the user to press a key. Doesn't fulfill
  the request.

## Behavior

The tool is **create-only**. It does NOT switch the active seat.

Rationale: the tool runs *inside the current persona's turn*. Calling
`_activate_seat` mid-turn is exactly the seat/model-leak hazard the C2c work
flagged (state leaking across an in-flight prompt). Create-only is both safer and
less code. The existing switch path (agents drawer) is unchanged; the agent's
success message points the user there.

### Flow

```
Agent emits tool call: create_persona {name: "Robbie"}
   ↓
tracing_agent._dispatch_tool → CreatePersonaTool.execute(args, env)
   ↓
slug = slugify_persona_name("Robbie")          → "robbie"
   ↓
persona.create_persona("robbie", display_name="Robbie")   # create-only
   ↓
returns {output: <friendly message>, returncode: 0, exception_info: None}
```

### Tool contract

- **name:** `create_persona`
- **schema:** one required param `name` (free-text display name). The tool
  slugifies it internally to derive the id. Description steers usage and states
  it does NOT switch.
- **display_label(args):** `create_persona <name>`
- **execute(args, env):** returns the Tool-protocol dict
  `{"output": str, "returncode": int, "exception_info": str | None}`. **Never
  raises into the dispatcher** — every failure returns `returncode: 1` (matches
  `CreateJobTool`).

### Success message (option B — sets expectations)

Confirms creation, gives the id, points to the agents drawer to switch, and notes
the persona starts blank (inert SOUL/IDENTITY/USER templates) so the user knows
to fill it in. Example:

> Created persona 'Robbie' (id: `robbie`). It starts blank — edit its
> SOUL.md / IDENTITY.md / USER.md to give it a personality. Switch to it from the
> agents drawer when you're ready.

### Error handling

`execute` catches and converts to a `returncode: 1` result (never raises):
- **missing/blank `name`** → "name required".
- **`PersonaExists`** → "A persona '<id>' already exists."
- **`InvalidPersonaId`** → only reachable when slugify yields `""` (e.g. "!!!")
  or `"default"`. → "Couldn't derive a valid persona id from that name."
- **`OSError`** → "Couldn't create persona: <err>".

## No gate wrapper (YAGNI)

`create_job` has a `handle_create_job` wrapper because it enforces a fail-closed
cost/grant gate. Persona-create's only gate is "valid id," already enforced inside
`persona.create_persona` (raises `InvalidPersonaId`/`PersonaExists`). A wrapper
would be ceremony with no gate to enforce. The tool calls `create_persona`
directly.

## Files

- **New:** `harness/tools/create_persona.py` — `CreatePersonaTool`.
- **Modify:** `harness/tools/registry.py` — import + add `CreatePersonaTool()` to
  the always-present list (it needs no roots/context, like `CreateJobTool`).
- **New:** `tests/test_create_persona_tool.py`.

## Tests

Unit tests on `CreatePersonaTool.execute`, using the existing `isolated_config`
fixture (`monkeypatch.setenv("XDG_CONFIG_HOME", tmp_path)` redirects
`paths.config_dir()`):

1. **Happy path** — `{"name": "Robbie"}` creates `agents/robbie/` with the
   template trio, writes "Robbie" to `persona.toml`, `returncode: 0`, message
   contains the id, the drawer hint, and the "starts blank" note.
2. **Slugification** — `{"name": "Robbie The Bold!"}` → asserts the id is exactly
   `slugify_persona_name("Robbie The Bold!")` (test the real function, not an
   assumption), dir created.
3. **Duplicate** — same name twice → second call `returncode: 1`, "already
   exists", does not clobber.
4. **Missing/blank name** — `{}` and `{"name": "  "}` → `returncode: 1`, "name
   required", no directory created.
5. **Invalid-after-slugify** — `{"name": "default"}` and `{"name": "!!!"}` →
   `returncode: 1`, friendly error, no crash, no directory.
6. **No seat switch** — a stub `env` whose `_active_persona` is unchanged after a
   successful create (guards against accidentally wiring in `_activate_seat`).
7. **Registry presence** — `build_registry(...)` includes a tool named
   `create_persona`; it satisfies the Tool protocol.

## Out of scope

- Router changes / a `persona_create` task type.
- Auto-switching to the new persona.
- Editing persona personality files (the tool seeds inert templates only).
