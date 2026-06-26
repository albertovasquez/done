# Phase A — Persona / workspace contract (foundation)

**Status:** design / spec (ready for writing-plans)
**Date:** 2026-06-26
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Roadmap:** [`2026-06-26-persona-fleet-design.md`](2026-06-26-persona-fleet-design.md) — Phase A
**Pressure-test:** [`2026-06-26-persona-fleet-pressure-test.md`](2026-06-26-persona-fleet-pressure-test.md)
**Seams (read 2026-06-26):** `harness/acp_agent.py`, `harness/acp_session.py`,
`harness/skills.py`, `harness/tracing_agent.py`, `harness/chat_handler.py`,
`harness/config.py`, `harness/paths.py`, `harness/router.py`, `harness/acp_main.py`

---

## 1. Purpose & scope

Define what a **persona is on disk** for DoneDone and inject it — the foundation
the rest of the persona fleet (memory, selection, onboarding, crons) builds on.

Phase A ships **one built-in `default` persona** and the plumbing to read its
workspace files and inject them into the model's context. It is deliberately
foundation-only.

**In scope**

- A persona-content module that reads a workspace's identity-trio files
  (`SOUL.md`, `IDENTITY.md`, `USER.md`) and composes one injectable block.
- Injection of that block into **both** user-facing dispatch paths (agent **and**
  chat) — the fix the pressure-test forced.
- A built-in `default` persona that is empty, so current behavior is unchanged.

**Out of scope (named here so the boundary is explicit)**

- Memory (`MEMORY.md`, `memory/*`) — Phase B.
- Multiple personas, `--persona` selection, the `/persona` picker — Phase C.
- `persona.toml` and model precedence (D4) — Phase C (where multiple personas
  with their own model pins actually exist).
- `AGENTS.md` and `TOOLS.md` injection — deferred to their consuming phases
  (`AGENTS.md`'s operating-manual semantics overlap the engine's own dispatch and
  need their own design pass).
- `BOOTSTRAP.md` / attestation — Phase D. `HEARTBEAT.md` / crons — Phase E.

---

## 2. The problem Phase A must solve (why this isn't the roadmap's Phase A verbatim)

The roadmap's §4 says persona context "becomes a second injected block alongside
the skill block — same seam." The pressure-test showed that seam reaches **one of
four dispatch paths**: the skill block is composed only on the agent path
(`acp_agent.py:162`) and appended only inside `TracingAgent`'s system template
(`tracing_agent.py:46`). The chat path (`ChatHandler`), the clarify/ambiguous
early returns, and the router itself never see it.

**Decision (this spec):** inject persona into the **agent and chat paths** — the
two paths that produce user-facing answers. The router stays a pure classifier
(persona-blind by design; routing is mechanical and persona belongs in the
answer, not the path choice). The clarify/ambiguous branches emit router
boilerplate, not model output, and are intentionally not personalized.

This means persona text must reach two consumers with **different injection
shapes**:

- **Agent path:** append to an existing system *template* (`tracing_agent.py:46`).
- **Chat path:** `ChatHandler` builds `messages` as `(history or []) + [user]`
  with **no system message at all** (`chat_handler.py:78`). Persona injection here
  means *adding a system message that does not exist today*.

The design below composes the block **once** and applies it at both sites per each
site's nature.

---

## 3. Architecture

### 3.1 New module: `harness/persona.py`

A content module parallel to `skills.py`. One job: read a workspace dir's
identity-trio files and compose them into one injectable block. It **only reads
files and returns data** — it never injects (consumers do) and never decides which
workspace (Phase C does).

```python
@dataclass
class PersonaLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)        # filenames composed in
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (filename, reason)

PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md"]   # order = injection order
MAX_FILE_CHARS = 8000                                   # per-file trim ceiling (tunable)

def compose_persona(workspace_dir: Path) -> PersonaLoad: ...
```

Design mirrors `SkillLoad` / `skills.compose` exactly:

- Same `injected` / `skipped` telemetry shape, so the existing `_meta` plumbing
  (`acp_agent.py:164` emits `skill_load`) gets a parallel `persona_load` event for
  free.
- **Every per-file read is individually wrapped** (`OSError`, `UnicodeDecodeError`)
  so one bad/missing file can never abort a turn — line-for-line the `skills.py`
  discipline.
- **Blank files are skipped** (recorded in `skipped` with reason `"blank"`), never
  injected as empty headers.
- **Oversized files are trimmed** to `MAX_FILE_CHARS` with a truncation marker
  (`\n\n…[truncated]…`). This is **new code** — `skills.compose` has no truncation
  (`skills.py:84`), contrary to the roadmap's "trim-truncate ports directly"
  claim. A small `_trim(text, limit) -> (text, was_trimmed)` helper lives in
  `persona.py`.

**Composed block shape** (labeled section, parallel to `# Available Skills`):

```
\n\n# Persona\n\n
You are operating as the following persona. Honor it.\n\n
## SOUL\n<soul body>\n\n
## IDENTITY\n<identity body>\n\n
## USER\n<user body>
```

Missing/blank files are simply absent from the block. When **all three** are
missing/blank, `block == ""` and `injected == []` — the no-op case that makes the
`default` persona invisible (§3.4).

### 3.2 Compose once, inject twice (single source of truth)

```
acp_agent.py prompt()
   │  (first turn of session only)
   ├─ persona.compose_persona(workspace)  ── run_in_executor (filesystem I/O)
   │        └─► PersonaLoad.block ──► cached on SessionState.persona_block
   │
   ├─ emit _meta {persona_load: {injected, skipped}}   (parallel to skill_load)
   │
   ├─ router.classify(...)            ← persona-blind, unchanged
   │
   ├─ chat_question  ─► ChatHandler(..., persona_block=state.persona_block)
   │                        prepends {role: system, content: block} every turn
   │
   └─ agent path     ─► TracingAgent(..., persona_block=state.persona_block)
                            appends block to system_template, first turn seeds once
```

- **Compose once:** read disk on the session's **first turn** only; cache the
  string on `SessionState.persona_block` (new field, default `""`). Later turns
  reuse the cached string — disk is read once per session, not per turn. This is
  also why mid-session edits to persona files are not picked up within a session
  (acceptable for static identity files; a known, documented property — see §6).

- **Agent path (first-turn-only seeding):** `TracingAgent.__init__` gains a
  `persona_block: str = ""` param. `_render_template` appends it to the **system
  template only**, *before* the skill block (identity first, then task skills):

  ```python
  if self._persona_block and template is self.config.system_template:
      out += self._persona_block
  if self._skill_block and template is self.config.system_template:
      out += self._skill_block
  ```

  First-turn-only falls out naturally: `TracingAgent.run` seeds `self.messages`
  with the system message once per turn, and the block is identical each turn.

- **Chat path (every turn):** `ChatHandler.__init__` gains `persona_block:
  str = ""`. `answer_stream` prepends a system message when the block is non-empty:

  ```python
  sys = [{"role": "system", "content": self._persona_block}] if self._persona_block else []
  messages = sys + (history or []) + [{"role": "user", "content": prompt}]
  ```

  Chat is stateless per call (re-reads `history` each time), so there is no "first
  turn" to anchor to — the system message goes on **every** chat turn. The
  capability-question fast path (`chat_handler.py:66`) and mock-mode path (`:69`)
  return before the model call and are unchanged — persona does not affect a
  catalog listing.

**Why single-source matters:** both sites consume the *same* `state.persona_block`
string. There is one read of disk, one trim policy, one composed block — two
injection sites, not two sources. This is the structural answer to the
pressure-test's "1 of 4 paths" finding.

### 3.3 `SessionState` gains one field

`acp_session.py` `SessionState` adds:

```python
persona_block: str | None = None   # None = not yet composed; "" = composed-empty
```

`None` vs `""` distinguishes "haven't read disk yet" (compose on this turn) from
"read disk, persona is empty" (don't re-read, it's a no-op). `prompt()` composes
when `state.persona_block is None`, else reuses. No persistence layer is added
(SessionStore stays in-memory — that is Phase B/C work per the pressure-test); the
cache lives only for the session's lifetime, which is exactly the
compose-once-per-session scope.

### 3.4 The built-in `default` persona & workspace location

- **Location:** a new `paths.default_workspace_dir() -> config_dir()/agents/default/`.
  Resolved through `paths.py` (the asset-resolution single source of truth), never
  via `__file__`/cwd assumptions — consistent with `skills_dirs()` and the
  wheel-install constraint.
- **`acp_main.py` wiring:** resolve the default workspace once at startup and pass
  it into `HarnessAgent` exactly as `skills_dir` is passed today (`acp_main.py:96`).
  `HarnessAgent.__init__` gains `workspace_dir: Path`.
- **Empty by default = no-op:** the shipped `default` workspace has no trio files
  (or empty ones). `compose_persona` returns `block == ""` → both injection sites
  short-circuit on the empty string → **agent and chat message lists are
  byte-identical to pre-persona behavior.** This is the load-bearing safety
  property and is locked by a test (§5).
- The absent-directory case is handled the same as empty: `compose_persona` on a
  non-existent dir returns an empty `PersonaLoad` (no raise), matching how
  `skills.load_catalog` treats absent roots (`skills.py:45`).

Note: `config.py` already anticipates a fleet — `RESERVED_KEY = "default"` is "the
always-present primary agent" and the module round-trips uuid-keyed named agents
(`config.py:9-12`). Phase A's `default` workspace aligns with that reserved key by
name; wiring `done.conf` model config *to* a persona is Phase C (D4), not here.

---

## 4. Data flow (one prompt turn)

1. `prompt()` is entered; `state = store.get(session_id)`.
2. If `state.persona_block is None`: `block = await run_in_executor(persona.compose_persona, workspace_dir)`; `state.persona_block = block.block`; emit `_meta {persona_load: {injected, skipped}}`.
3. Router classifies (unchanged, persona-blind).
4. Dispatch:
   - `chat_question` → `ChatHandler(model_id, catalog=..., persona_block=state.persona_block)` → system message prepended when non-empty.
   - agent path → `skills.compose` (unchanged) → `TracingAgent(..., persona_block=state.persona_block, skill_block=load.block)` → both appended to system template, persona first.
   - clarify / ambiguous → unchanged (no persona; router boilerplate).
5. Record/extend transcript (unchanged).

---

## 5. Testing

Project discipline: `tests/` only, `.venv/bin/python -m pytest tests/ -q`.

**Unit — `persona.compose_persona`** (mirrors the skills-compose tests):

- All three files present → `block` contains all three labeled sections in order;
  `injected == ["SOUL.md", "IDENTITY.md", "USER.md"]`.
- Partial (e.g. only `SOUL.md`) → block has only present sections; absent files in
  `skipped` is **not** required (absence is silent, like skills) — assert they are
  simply not in `injected`.
- All missing / non-existent dir → `block == ""`, `injected == []`, no raise.
- Blank file → recorded in `skipped` with reason `"blank"`, not injected.
- Oversized file → body trimmed to `MAX_FILE_CHARS`, truncation marker present,
  `injected` still lists it.
- Unreadable / non-UTF-8 file → in `skipped` with a reason, turn not aborted.

**Integration — injection reach (the pressure-test regression, locked):**

- Non-empty persona block ⇒ it appears in the **agent path** system message
  (assert the composed system template contains the persona header) **and** in the
  **chat path** message list (assert `ChatHandler` produced a `role: system`
  message with the block). This single test would have caught the original "1 of 4
  paths" design flaw.
- Persona is injected **before** skills in the agent system template (ordering).

**Integration — default no-op (the safety property):**

- Empty/absent default workspace ⇒ agent path system message and chat path
  message list are **byte-identical** to the pre-persona construction. Guards
  against any accidental whitespace/header leaking into the no-op case.

**Meta event:** a `persona_load` `_meta` event is emitted with `injected`/`skipped`
(parallel to `skill_load`), so a client can show what persona context loaded.

---

## 6. Known properties & deferred items (honest debt)

- **Mid-session edits not picked up.** Persona is composed once per session and
  cached on `SessionState` (§3.3). Editing `SOUL.md` mid-session has no effect
  until the next session. Acceptable for static identity files; revisit if Phase B
  (memory) needs intra-session refresh.
- **Router stays persona-blind.** A persona cannot bias how its own turns are
  classified (`router.py` fixed prompt). This is an accepted ceiling on
  "drift" (D3) — documented, not fixed, in Phase A.
- **No `persona.toml`, no model precedence.** Deferred to Phase C with multiple
  personas (D4). Phase A is one fixed workspace.
- **No persistence of the persona block beyond session lifetime.** Matches the
  in-memory SessionStore; durable per-persona state is Phase B/C.
- **`MAX_FILE_CHARS` is a single flat ceiling**, not the smarter compaction the
  roadmap §6 flags for memory. Sufficient for static identity files; memory
  compaction is a Phase B problem.

---

## 7. Files touched

| File | Change |
|---|---|
| `harness/persona.py` | **new** — `PersonaLoad`, `compose_persona`, `_trim` |
| `harness/paths.py` | add `default_workspace_dir()` |
| `harness/acp_session.py` | `SessionState.persona_block: str \| None = None` |
| `harness/acp_agent.py` | compose-once on first turn; emit `persona_load`; thread `persona_block` into `ChatHandler` and `TracingAgent`; `__init__` gains `workspace_dir` |
| `harness/tracing_agent.py` | `__init__` `persona_block` param; append in `_render_template` before skills |
| `harness/chat_handler.py` | `__init__` `persona_block` param; prepend system message in `answer_stream` |
| `harness/acp_main.py` | resolve default workspace, pass to `HarnessAgent` |
| `tests/` | persona-compose unit tests; injection-reach regression; default no-op |

Bundled default workspace: ship an empty `harness/skills`-style asset or rely on
the absent-dir no-op (decide in writing-plans — absent-dir is simpler and already
a no-op).

---

## 8. Success criteria

1. `compose_persona` reads the trio, trims, skips blanks, never raises — covered
   by unit tests.
2. A non-empty persona block reaches **both** the agent and chat paths — locked by
   the injection-reach regression test (the pressure-test's headline finding).
3. The empty `default` persona produces **byte-identical** behavior to today — the
   no-op safety test passes.
4. `.venv/bin/python -m pytest tests/ -q` is green.
5. No `persona.toml`, memory, selection, or cron code is introduced (scope held).
