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

### 2.1 The forward-compatibility requirement (why this is a foundation, not a patch)

This spec is the **first phase of a multi-agent product**, not a one-client
feature. The persona fleet's later phases each add a *new consumer* of the engine:
the cron client (Phase E), sub-agents (Phase F), and the GitHub PR worker (the
linear roadmap's Phase 7) all construct an agent turn. If persona is threaded
**per construction site** — a `persona_block=` parameter passed into each
`TracingAgent`/`ChatHandler` call — then **every future consumer must remember to
re-thread it**, and the "1 of N paths" bug the pressure-test killed returns as a
recurring tax on growth. That is the difference between "a harness with personas on
one client" and "a tool where persona is a property of the engine."

**Decision (this spec):** persona is resolved at **one chokepoint that every
dispatch path flows through**, not threaded into each constructor. Concretely,
there is already a shared operation in the codebase — *"resolve the injectable
context block for this turn from disk, then hand it to the agent"* — performed by
both `acp_agent.py:162` (skills on the agent path) and `run_traced.py:94`
(`load_skills` → `run_agent`). Persona is **the same operation with a second
source**. Phase A introduces a single `compose_context()` resolver that returns the
combined persona+skill block, and makes the construction sites consume *its*
output. New consumers inherit personas by calling the resolver, not by adding a
parameter. See §3.2.

**Scope honesty:** `harness/run_traced.py` is the Phase-0 *developer* entrypoint
(it resolves assets via `REPO_ROOT`/`__file__`, pre-distributability), not a
shipped product surface. It is wired through the same resolver for consistency and
to prove the chokepoint generalizes, but it is not the reason the chokepoint
exists — the future *product* consumers (E/F/7) are.

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
  (`acp_agent.py:165` emits the `skill_load` key) gets a parallel `persona_load`
  event for free.
- **Every per-file read is individually wrapped** (`OSError`, `UnicodeDecodeError`)
  so one bad/missing file can never abort a turn — line-for-line the `skills.py`
  discipline.
- **Absent-directory guard is new code.** `compose_persona` reads fixed filenames
  in one dir, so it needs its own top-level `if not workspace_dir.is_dir(): return
  PersonaLoad()` guard. `skills.compose` (`skills.py:63`) has *no* such guard — it
  relies on per-skill `is_file()` checks — so this is **not** mirrored from it
  (same caveat as `_trim` below). A non-existent or empty workspace must yield an
  empty `PersonaLoad`, never raise.
- **Blank files are skipped.** "Blank" means **empty after `.strip()`** — a file
  containing only whitespace/newlines is treated as blank, recorded in `skipped`
  with reason `"blank"`, and never injected. This is load-bearing for the
  byte-identical no-op (§3.4): a whitespace-only file must not produce a truthy
  block that injects an empty system message.
- **Oversized files are trimmed** to `MAX_FILE_CHARS` with a truncation marker
  (`\n\n…[truncated]…`). This is **new code** — `skills.compose` has no truncation
  (`skills.py:84`), contrary to the roadmap's "trim-truncate ports directly"
  claim. A small `_trim(text, limit) -> (text, was_trimmed)` helper lives in
  `persona.py`. The cap is **per file**; with three files the worst-case persona
  budget is `3 × MAX_FILE_CHARS`. No aggregate cap in Phase A (acceptable for
  static identity files); an aggregate budget is a Phase B concern alongside memory
  compaction.

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

### 3.2 The resolution chokepoint (single source of truth)

The structural answer to "persona must reach every consumer without per-site
re-wiring" (§2.1) is **one resolver, two distinctions**:

- **Distinction 1 — string composed once, injected per turn.** The persona *string*
  is read from disk and composed **once per session** (cached on `SessionState`).
  It is *injected* into a fresh message list on **every** turn, at every site —
  because both the agent path (`TracingAgent.run` rebuilds `self.messages = []`
  each turn, `tracing_agent.py:67`) and the chat path (`ChatHandler` is
  reconstructed per turn, `acp_agent.py:137`) build their context from scratch each
  time. "Once" applies to the *disk read*, not the injection. (This is why
  mid-session file edits are not picked up — the cached string, not the injection,
  is what's frozen; see §6.)

- **Distinction 2 — one compose call, fanned to all sites.** A single resolver owns
  "what context does this turn inject":

  ```python
  # harness/persona.py
  @dataclass
  class TurnContext:
      persona_block: str = ""     # from compose_persona(workspace)
      skill_block: str = ""       # from skills.compose(roots, names)

  def compose_context(workspace_dir, skill_roots, skill_names) -> TurnContext: ...
  ```

  Every entrypoint calls `compose_context` and passes the resulting `TurnContext`
  into the agent/chat constructors. The persona half is cached per session (below);
  the skill half is already per-turn (router-selected names change per request).
  New consumers (cron client, sub-agents, GitHub worker) call `compose_context`
  and get persona **for free** — they cannot accidentally ship persona-blind.

```
prompt()  (and every other entrypoint)
   │
   ├─ persona half: if state.persona_block is None:                  ← first disk read
   │      state.persona_block, persona_meta =
   │          await run_in_executor(persona.compose_persona, workspace)
   │      if persona_meta.injected:                                  ← GATED emit (§ below)
   │          emit _meta {persona_load: {injected, skipped}}
   │   else: reuse cached state.persona_block
   │
   ├─ router.classify(...)            ← persona-blind, unchanged
   │
   ├─ skill half (agent path only): load = skills.compose(roots, cls.skills)
   │
   ├─ ctx = TurnContext(persona_block=state.persona_block, skill_block=load.block)
   │
   ├─ chat_question  ─► ChatHandler(..., persona_block=ctx.persona_block)
   │                        prepends {role: system} every turn IFF non-empty
   │
   └─ agent path     ─► TracingAgent(..., persona_block=ctx.persona_block,
                                          skill_block=ctx.skill_block)
                            appends to system_template every turn IFF non-empty
```

- **Agent path injection.** `TracingAgent.__init__` gains `persona_block: str = ""`.
  `_render_template` appends it to the **system template only**, *after* the
  Jinja-rendered base and *before* the skill block (base → persona → skills:
  identity precedes task skills, both follow the engine's own core prompt):

  ```python
  out = super()._render_template(template)               # base
  if self._persona_block and template is self.config.system_template:
      out += self._persona_block
  if self._skill_block and template is self.config.system_template:
      out += self._skill_block
  return out
  ```

  The `template is self.config.system_template` identity check is the existing
  skill-block pattern (`tracing_agent.py:46`) — `_render_template` is called twice
  per run (system + instance), and only the system call matches, so the instance
  template is never personalized. Verified sound against the live reimplemented
  `run()` (`tracing_agent.py:62-71`).

- **Chat path injection.** `ChatHandler.__init__` gains `persona_block: str = ""`.
  `answer_stream` prepends a system message **only when non-empty**:

  ```python
  sys = [{"role": "system", "content": self._persona_block}] if self._persona_block else []
  messages = sys + (history or []) + [{"role": "user", "content": prompt}]
  ```

  The capability-question fast path (`chat_handler.py:66`) and mock-mode path
  (`:69`) return before the model call and are unchanged — persona never re-voices
  a deterministic catalog listing. (§2 frames persona as reaching the two paths
  that produce **model-generated** answers; the catalog/mock sub-paths produce
  deterministic text and are deliberately persona-blind.)

**The `persona_load` meta event is GATED.** Unlike the prose draft of the roadmap,
the event fires **only when `persona_meta.injected` is non-empty** — i.e. never for
the empty `default` persona. This is required for the byte-identical no-op (§3.4):
the parallel `skill_load` event (`acp_agent.py:163-165`) fires only on the agent
path after the chat/clarify early returns, so emitting an *un*gated `persona_load`
at the top of `prompt()` would add an observable `_meta` notification on every turn
(including chat/clarify) even for the empty default — a behavior change a client
could see. Gating on non-empty `injected` makes the empty default emit nothing.

### 3.3 `SessionState` gains one field

`acp_session.py` `SessionState` adds **exactly**:

```python
persona_block: str | None = None   # None = not-yet-composed; "" = composed-empty
```

The default **must be `None`**, not `""`. The first-turn detection is
`if state.persona_block is None`; a `""` default would make that check never fire,
disk would never be read, and persona would silently never load — masked by the
empty-default no-op so tests stay green while the feature does nothing. `None`
(haven't read disk) vs `""` (read disk, persona is empty) is the whole point of the
sentinel. No persistence layer is added (`SessionStore` stays in-memory — Phase
B/C work); the cache lives for the session lifetime, which is exactly the
compose-once-per-session scope.

**Concurrency note.** `prompt()` reads `state.persona_block`, `await`s the executor
compose, then writes back. `SessionStore` is an unlocked in-memory dict and the
harness already assumes **serial prompts per session** (cf. the existing unlocked
`state._last_tc_id` mutation across turns). Two pipelined prompts on one session
could both compose; this is benign (deterministic compose → same string) but means
"one disk read per session" holds only under the serial-prompt precondition the
harness already relies on. Stated, not guarded — guarding is out of scope for A.

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
  non-existent dir returns an empty `PersonaLoad` (no raise), via its own
  `is_dir()` guard (§3.1) — analogous to, but not copied from, how
  `skills.load_catalog` skips absent roots (`skills.py:45`).

Note: `config.py` already anticipates a fleet — `RESERVED_KEY = "default"` is "the
always-present primary agent" and the module round-trips uuid-keyed named agents
(`config.py:9-12`). Phase A's `default` workspace aligns with that reserved key by
name; wiring `done.conf` model config *to* a persona is Phase C (D4), not here.

---

## 4. Data flow (one prompt turn)

1. `prompt()` is entered; `state = store.get(session_id)`.
2. **Persona (cached):** if `state.persona_block is None`, `load = await run_in_executor(persona.compose_persona, workspace_dir)`; set `state.persona_block = load.block`; **iff `load.injected`** (non-empty), emit `_meta {persona_load: {injected, skipped}}`. Else reuse the cached string and emit nothing.
3. Router classifies (unchanged, persona-blind).
4. Dispatch:
   - `chat_question` → `ChatHandler(model_id, catalog=…, persona_block=state.persona_block)` → system message prepended **iff non-empty**.
   - agent path → `skills.compose` (unchanged) → `TracingAgent(…, persona_block=state.persona_block, skill_block=load.block)` → each appended to the system template **iff non-empty**, base → persona → skills.
   - clarify / ambiguous → unchanged (no persona; router boilerplate).
5. Record/extend transcript (unchanged).

The persona read (step 2) and the skill compose (step 4 agent path) are the two
halves the `compose_context` resolver (§3.2) unifies; an entrypoint that has no
router (e.g. a future cron consumer firing a fixed prompt) calls `compose_context`
with an empty skill-name list and still gets persona.

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
- Blank file (empty) → in `skipped` with reason `"blank"`, not injected.
- **Whitespace-only file** (only spaces/newlines) → treated as blank (empty after
  `.strip()`), in `skipped`, **`block == ""`** — guards the byte-identical no-op
  against a truthy-but-empty block.
- Oversized file → body trimmed to `MAX_FILE_CHARS`, truncation marker present,
  `injected` still lists it.
- Unreadable / non-UTF-8 file → in `skipped` with a reason, turn not aborted.

**Integration — injection reach (the pressure-test regression, locked):**

- Non-empty persona block ⇒ it appears in the **agent path** system message
  (assert the composed system template contains the persona header) **and** in the
  **chat path** message list (assert `ChatHandler` produced a `role: system`
  message with the block). This locks the regression of persona reaching only the
  agent path. (The deliberate exclusions — router, clarify/ambiguous, catalog/mock
  sub-paths — are *not* personalization targets and are asserted absent by the
  no-op test.)
- Persona is injected **before** skills and **after** the base in the agent system
  template (ordering: base → persona → skills).
- **Chokepoint coverage (forward-compat lock):** `compose_context` returns a
  `TurnContext` carrying both blocks; a unit test asserts a consumer built from its
  output injects persona without any per-site `persona_block=` plumbing. This is
  the test that keeps a *future* consumer (cron/sub-agent/GitHub worker) from
  shipping persona-blind.

**Integration — default no-op (the safety property):**

- Empty/absent default workspace ⇒ agent path system message and chat path
  message list are **byte-identical** to the pre-persona construction. Guards
  against any accidental whitespace/header leaking into the no-op case.
- **Gated meta event:** empty default ⇒ **no** `persona_load` `_meta` event is
  emitted (on any turn type, including chat/clarify). A non-empty persona ⇒ exactly
  one `persona_load` on the session's first turn. This locks Finding-3 (an ungated
  emit would break byte-identicality).

---

## 6. Known properties & deferred items (honest debt)

- **Mid-session edits not picked up.** Persona is composed once per session and
  cached on `SessionState` (§3.3). Editing `SOUL.md` mid-session has no effect
  until the next session. Acceptable for static identity files; revisit if Phase B
  (memory) needs intra-session refresh.
- **Router stays persona-blind.** A persona cannot bias how its own turns are
  classified (`router.py` fixed prompt). This is an accepted ceiling on
  "drift" (D3) — documented, not fixed, in Phase A.
- **No `persona.toml`, no model precedence.** The pressure-test (§6.4) asked for
  the model-precedence ladder *in Phase A*. We deliberately defer it to Phase C:
  Phase A ships a **single fixed workspace** with no `persona.toml`, so there is no
  second model-writer and nothing to order yet — the precedence bug cannot exist
  until multiple personas with model pins do. Revisit in C **before** any
  `persona.toml` model pin lands (it remains Codex-review territory per CLAUDE.md).
- **No persistence of the persona block beyond session lifetime.** Matches the
  in-memory SessionStore; durable per-persona state is Phase B/C.
- **`MAX_FILE_CHARS` is a single flat ceiling**, not the smarter compaction the
  roadmap §6 flags for memory. Sufficient for static identity files; memory
  compaction is a Phase B problem.

---

## 7. Files touched

All five construction/wiring sites are listed — the chokepoint (§2.1) means each
must consume `compose_context`'s output, not re-implement persona threading.

| File | Change |
|---|---|
| `harness/persona.py` | **new** — `PersonaLoad`, `compose_persona`, `_trim`, the absent-dir guard, and the `TurnContext` + `compose_context(workspace, skill_roots, skill_names)` resolver (§3.2) |
| `harness/paths.py` | add `default_workspace_dir() -> config_dir()/agents/default/` |
| `harness/acp_session.py` | `SessionState.persona_block: str \| None = None` (default **None**, §3.3) |
| `harness/acp_agent.py` | compose-once on first turn via the resolver; **gated** `persona_load` emit (iff `injected`); thread `persona_block` into `ChatHandler` and `TracingAgent`; `HarnessAgent.__init__` gains `workspace_dir` |
| **`harness/acp_agent.py` `build_harness_agent`** | factory (`:288`) gains `workspace_dir` and forwards it — **else `tests/test_acp_session_context.py` breaks at construction** |
| `harness/tracing_agent.py` | `__init__` `persona_block` param; append in `_render_template` (base → persona → skills) |
| `harness/chat_handler.py` | `__init__` `persona_block` param; prepend system message in `answer_stream` iff non-empty |
| `harness/acp_main.py` | resolve default workspace (`paths.default_workspace_dir()`), pass to `HarnessAgent` |
| **`harness/run_traced.py` / `harness/runner.py`** | non-ACP dev path: route persona through the same resolver. `MiniSweAgentRunner.run` (`runner.py:83`) already takes `skill_block=`; it gains the `TurnContext` (or a `persona_block=`) so `run_traced` is not silently persona-blind. **In scope to prove the chokepoint generalizes** (§2.1) — flag in writing-plans if it proves heavier than expected and split to a fast-follow. |
| `tests/` | persona-compose unit tests; injection-reach regression; chokepoint coverage; gated-event; default no-op |

Bundled default workspace: rely on the **absent-dir no-op** (simpler; already a
no-op) rather than shipping an empty asset — confirm in writing-plans.

---

## 8. Success criteria

1. `compose_persona` reads the trio, trims oversized files (marker present), skips
   blank/whitespace-only files, guards the absent dir, never raises — unit tests.
2. A non-empty persona block reaches **both** the agent and chat paths — locked by
   the injection-reach regression test (the pressure-test's headline finding).
3. The empty `default` persona produces **byte-identical** behavior to today —
   including **no `persona_load` event** — the no-op + gated-event tests pass.
4. **Forward-compat:** a consumer built from `compose_context`'s `TurnContext`
   injects persona with no per-site plumbing — the chokepoint test passes, so a
   future consumer cannot ship persona-blind.
5. `.venv/bin/python -m pytest tests/ -q` is green (existing suite + new tests;
   `test_acp_session_context.py` still passes through the updated factory).
6. **Scope gate (review-checklist, not a test):** `git diff --stat` touches only
   the §7 files; `harness/persona.py` imports no `toml`/memory/selection/cron
   module. (Absence of out-of-scope code can't be asserted by a passing suite, so
   this is a merge-review check, not a unit test.)
