# Teach the agent its persona files (base prompt) — design

**Date:** 2026-06-28 · **Base:** `main` @ `8e44534` (persona-slugify merged, PR #76) ·
**Worktree:** `.claude/worktrees/persona-files-prompt` (branch `persona-files-prompt`).

A small, focused addition: the agent's base prompt gains knowledge of its persona files
(SOUL/IDENTITY/USER.md) — their purpose AND the concrete path — so it can Read/Edit them
when the user asks to update its persona.

---

## 1. The gap (verified against live code)

Today the agent has the Read/Write/Edit tools (PR #72) but does NOT know its persona files
exist or where they are:
- `base_prompt.py` covers security, working principles, and the environment block —
  nothing about SOUL/IDENTITY/USER.md.
- `compose_persona` injects the file *content* under `# Persona`, but never the
  **workspace path**.
- The agent runs in the PROJECT cwd, not the persona dir — so a relative `SOUL.md` won't
  resolve; it needs the absolute `~/.config/harness/agents/<id>/` path.

Result: "update your SOUL.md to be more concise" fails — the agent can't locate the file.

## 2. The decisions (locked in brainstorm, 2026-06-28)

1. **Where:** the **base prompt** (`render_base_prompt`), always present.
2. **Concrete path:** name the **active** persona id + absolute workspace path (threaded in
   at the call site, like `cwd`/`model`/`system_line` already are) — not a generic `<id>`.
   No guessing which persona is active.
3. **Default included:** the section renders for the `default` persona too (its files are
   the inert templates, and editing them is how a user customizes the default). One code
   path, no `if persona_id != "default"` branch.

## 3. Architecture

`render_base_prompt` stays **pure** (values in, string out, no I/O). It gains two new
OPTIONAL keyword args; the active persona id + absolute path are **resolved at the call
site** (`acp_agent.py:360`, where `state.workspace_dir` is in scope) and passed in. base_prompt
does NO path resolution.

**Signature change** (`harness/base_prompt.py`):
```python
def render_base_prompt(*, model_id: str, cwd: str, system_line: str,
                       cutoff: str = KNOWLEDGE_CUTOFF,
                       persona_id: str | None = None,
                       persona_dir: str | None = None) -> str:
```
When BOTH `persona_id` and `persona_dir` are provided, a `# Persona files` section is
appended; when either is absent, the section is OMITTED (byte-identical to today for
callers that don't pass them).

**The rendered section:**
```
\n\n# Persona files
You are running as the persona "<persona_id>". Its files live in <persona_dir> :
- SOUL.md — your tone, behavior, and boundaries
- IDENTITY.md — your name, vibe, and emoji
- USER.md — who the user is and how they want to be addressed
When the user asks you to update your persona — your soul, identity, how you behave, or
what you know about them — Read and then Edit the relevant file in that directory.
```

**Call site** (`harness/acp_agent.py:360`): pass the resolved values from the session's
workspace:
```python
        ws = state.workspace_dir
        base_block = base_prompt.render_base_prompt(
            model_id=(model_id or "mock"),
            cwd=state.cwd, system_line=platform.platform(),
            persona_id=(ws.name if ws else None),
            persona_dir=(str(ws) if ws else None))
```
`state.workspace_dir` is always a real path in the live app (default → `default_workspace_dir()`,
named → `agents/<id>`), so the section always renders there. The `if ws else None` keeps it
robust if a workspace is ever absent.

## 4. Components & data flow

```
prompt() → state.workspace_dir (always real: default or named)
  → call site computes persona_id = ws.name, persona_dir = str(ws)
  → render_base_prompt(..., persona_id, persona_dir)
       both present → append "# Persona files" naming the 3 files + the abs path
       either absent → omit the section (byte-identical)
  → base_block flows into BOTH the chat path (ChatHandler base_block=)
    and the agent path (_run_agent_turn base_block=) — already wired (slugify branch
    preserved them)
  → the LLM now sees, every turn: the files, their purpose, and the concrete path to edit
```

When the user says "make your replies terser" / "update your soul", the agent has the
absolute path and the Edit tool → it edits `<persona_dir>/SOUL.md`.

## 5. Error handling / edge cases

- **No workspace** (`persona_id`/`persona_dir` absent): section omitted; the rest of the
  base block is byte-identical to today. Existing tests that call `render_base_prompt`
  without the new args are unaffected.
- **Pure function preserved:** base_prompt does NO `Path` ops, NO `config_dir()` read — it
  only string-formats the values passed in. The call site owns resolution (it already
  reads `state.workspace_dir`).
- **Path display:** pass the absolute path verbatim (`str(ws)`), e.g.
  `/Users/alberto/.config/harness/agents/fred`. No `~` expansion games — the agent's tools
  take absolute paths, and `str(ws)` is already absolute.
- **No content/no-op impact:** this changes the always-present base block only. The
  persona CONTENT injection (`compose_persona`) and the no-persona byte-identical guarantee
  for *injected content* are untouched. The base prompt is "always present by design", so
  adding a section to it is consistent with its contract.

## 6. Testing (TDD)

| Unit | Test |
|---|---|
| `render_base_prompt` with persona args | output contains `# Persona files`, the `persona_id`, the `persona_dir`, and all three filenames (SOUL.md/IDENTITY.md/USER.md) with their purpose |
| `render_base_prompt` without persona args | output has NO `# Persona files` section; the rest is byte-identical to the pre-change render (assert the section marker is absent + the Environment block still present) |
| `render_base_prompt` default persona | `persona_id="default", persona_dir=".../agents/default"` → section renders (no special-casing of "default") |
| purity | `render_base_prompt` does no I/O — same inputs → same output; no filesystem access (the function body has no Path/open/config_dir calls — a code-level assertion, plus the existing "pure render" tests stay green) |
| call site (prompt-driving) | a prompt run with an active persona produces a system/base prompt containing the active workspace path — verified via the existing prompt-driving harness in `tests/test_acp_session_context.py` (the base_block reaches the model) |

**Test-harness reminders:** base_prompt tests live in `tests/test_base_prompt.py`.
Prompt-driving (base_block reaches the model) is in `tests/test_acp_session_context.py`.
Editable-install shadowing — run worktree pytest with the WORKTREE as cwd.

## 7. Guardrails

- **base_prompt stays pure** — no I/O, no path resolution inside; resolved values passed in.
- **New args optional + omit-when-absent** — byte-identical for callers that don't pass
  them (existing tests unaffected).
- **No new branch on "default"** — one code path; default renders like any persona.
- **Additive only** — no change to persona content injection, the no-op guarantee for
  injected content, or the chat/agent base_block wiring.
- **Work in the worktree** (AGENTS.md #1); editable-install shadowing (§6).

## 8. Crux for review

- The call-site change must pass the ACTIVE session's workspace (`state.workspace_dir`),
  not the per-agent `self._workspace_dir` — so after a C2c persona switch the path matches
  the seat actually serving the turn. (Mirror how persona/memory already resolve from
  `state.workspace_dir`.) This is the one correctness point worth a careful look.

## 9. Definition of done

- Every turn, the agent's base prompt names its 3 persona files, their purpose, and the
  concrete absolute path of the ACTIVE persona (default or named).
- "Update your SOUL.md / make yourself more concise" → the agent Reads + Edits the right
  file (it has the path + the Edit tool).
- base_prompt stays pure; callers without the new args are byte-identical.
- Full suite green; PR against `main`; shipped.
