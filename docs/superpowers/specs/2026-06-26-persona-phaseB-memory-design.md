# Persona Phase B — memory + isolation core (design)

**Status:** design / spec (ready for writing-plans)
**Date:** 2026-06-26
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Tracker:** GitHub issue #28
**Builds on:** Phase A persona contract (merged PR #20) + install-seeded templates
(merged PR #27) + the core refactor (merged PR #31 — turn-local ids, the
`compose_context` chokepoint).
**Roadmap:** `2026-06-26-persona-fleet-design.md` (Phase B).

---

## 1. Purpose & the load-bearing principle

A persona today is **static** — identity files (`SOUL.md`/`IDENTITY.md`/`USER.md`)
and nothing more. The roadmap's principle D3 is that personas are *untyped and
mutable*, and **memory is the evolution mechanism**: a persona drifts toward
coding or assistant work by accumulating decisions/patterns in `MEMORY.md`, not
via any type field. Phase B makes that real.

Phase B has two halves:

- **Memory** — read a persona's memory files and inject them; let the agent write
  to them via a prompt-injected protocol + plain shell.
- **Isolation core** — make the persona workspace a **per-session** property of
  `SessionStore`/`SessionState` (pulled forward from Phase C), so memory is keyed
  per-workspace and multiple agent dirs *can* coexist — **without** any
  user-facing selection yet.

**Decisions settled in brainstorming (load-bearing):**

- **D-B1 — Scope:** isolation core + memory mechanism in B; selection UX (`--persona`
  / `/persona` / `persona.toml`) stays in C. B builds the pipe; C adds the valve.
- **D-B2 — Memory home:** per-workspace, keyed by the session's workspace dir.
- **D-B3 — Write:** agent-driven via a prompt-injected memory protocol + plain
  shell writes (append-only, read-before-write, no empty placeholders). NO new
  engine mechanism, NO new binary — the agent acts only through `BASH_TOOL`
  (`streaming_model.py:43`), so memory writes are ordinary shell.
- **D-B4 — Read timing:** memory injects **first-turn-only** (like persona),
  cached per session. A mid-session write is picked up next session; the agent can
  `cat` its own memory file mid-session if it wants a fresh read. Cross-session IS
  the evolution story.
- **D-B5 — Memory-protocol injection:** the write protocol is **identity-level,
  always present** when a persona has a memory dir (not router-gated) — it rides
  the same chokepoint as the memory block, not the skill catalog.

---

## 2. On-disk shape

Memory lives in the persona workspace, beside the identity trio:

```
~/.config/harness/agents/<id>/
├── SOUL.md, IDENTITY.md, USER.md     (Phase A — identity)
├── MEMORY.md                          (durable, long-term — the persona grows this)
└── memory/
    ├── 2026-06-26.md                  (today's working notes)
    └── 2026-06-25.md                  (yesterday)
```

Three readable sources: `MEMORY.md` + today's + yesterday's daily files. All
optional — absent/blank/comment-only = skipped (reuses Phase A's `_meaningful`
and `_trim`). Nothing is auto-created (consistent with Phase A; templates are a
Phase-A concern, and memory files are grown, not seeded).

---

## 3. The read side — `harness/memory.py`

A new content module parallel to `persona.py`. One job: read a workspace's memory
files and compose one injectable block. Reads files, returns data; never injects,
never selects the workspace.

```python
@dataclass
class MemoryLoad:
    block: str = ""                                          # protocol preamble + composed files
    injected: list[str] = field(default_factory=list)        # memory filenames composed in
    skipped: list[tuple[str, str]] = field(default_factory=list)
    has_workspace: bool = False                              # the workspace dir EXISTS (drives the protocol; see §5)

MEMORY_FILE = "MEMORY.md"
MEMORY_DIR = "memory"
MAX_MEMORY_CHARS = 8000          # per-file trim ceiling (reuse persona's value/helper)

def resolve_memory(workspace_dir: Path | None, *, today: date) -> MemoryLoad: ...
```

- Reads `MEMORY.md` (durable) + `memory/<today>.md` + `memory/<yesterday>.md`.
  **`today` is passed in, not `date.today()`** — testability + the codebase's
  no-ambient-clock discipline (`yesterday = today - timedelta(days=1)`).
- Reuses `persona._meaningful` and `persona._trim` (promote them to shared helpers
  or import — see §7). Blank/comment-only files skip; oversized files trim with the
  marker. **Same discipline as `compose_persona`** — a bad file never aborts a turn.
- Block shape (labeled, parallel to `# Persona`):
  ```
  \n\n# Memory\n\n
  You have persistent memory. Honor and extend it (see protocol).\n\n
  ## MEMORY.md\n<durable body>\n\n
  ## memory/<today>\n<today body>\n\n
  ## memory/<yesterday>\n<yesterday body>
  ```
- **Block composition** depends on the workspace existing (§5's rule):
  - workspace absent ⇒ `MemoryLoad()` empty, `has_workspace=False`, `block==""`;
  - workspace present, all three files missing/blank ⇒ `has_workspace=True`,
    `injected==[]`, `block ==` protocol preamble only;
  - workspace present with content ⇒ `block ==` preamble + composed files.

### 3.1 Chokepoint integration

`compose_context` gains a `memory_block` param; `TurnContext` gains a
`memory_block` field. Memory is resolved once per session (like persona) and
threaded through the chokepoint to both dispatch paths.

```python
def compose_context(persona_block: str, memory_block: str,
                    skill_roots: list[Path], skill_names: list[str]) -> TurnContext: ...
```

System-prompt order: **base → persona → memory → skills** (identity → what it's
learned → task skills). In `TracingAgent._render_template`, append `memory_block`
between persona and skills (same `template is self.config.system_template` guard).

Chat path (`ChatHandler`): the memory block is appended to the persona system
message (one identity-level system message carrying persona + memory), so chat
turns are also memory-aware.

---

## 4. The isolation core (pulled-forward Phase-C plumbing)

Today `HarnessAgent._workspace_dir` is a single per-agent value (always the
default). Phase B makes the workspace a **per-session** property so memory/persona
resolve per session, and multiple workspaces *can* coexist — the seam Phase C's
selection will set.

- `SessionState` gains `workspace_dir: Path | None = None` (beside the persona
  fields). It carries the workspace that session uses.
- `SessionStore.new(cwd, workspace_dir)` records it.
- `HarnessAgent.new_session` passes `self._workspace_dir` as the session's
  workspace (still always the default in B — no selection). The wiring is now
  per-session, not per-agent.
- Persona + memory resolve from `state.workspace_dir`, not `self._workspace_dir`.
- `SessionState` gains `memory_block: str | None = None` + `memory_load` +
  `memory_load_emitted` (mirrors the persona-cache + gated-emit trio from Phase A).

**Scope guard:** NO `--persona` flag, NO `/persona` picker, NO `persona.toml`, NO
multi-persona selection logic. B proves the plumbing is per-session (a test
constructs two sessions with different workspace dirs and asserts their
memory/persona are isolated); C adds the user-facing valve. This is the one part
of B that is "more than memory," and it is deliberately minimal.

---

## 5. The write side — the memory protocol (D3, prompt-driven)

The agent acts only through shell (`BASH_TOOL`). So the "memory-write tool" is a
**prompt-injected protocol**, not a new mechanism.

**Precise injection rule (resolves the no-op boundary):** the protocol preamble
appears **iff the session's workspace directory EXISTS** — independent of whether
any memory file has content yet. This way a brand-new (but real) persona learns it
*can* record from turn one, even with empty memory; and the byte-identical no-op is
scoped to an **absent** workspace (no `~/.config/harness/agents/<id>/` at all),
exactly matching Phase A's no-op semantics (`resolve_persona(absent) == empty`).

Concretely: `resolve_memory` returns, alongside the (possibly empty) memory block,
a `has_workspace: bool`. The injected memory section is:
- workspace absent ⇒ `""` (strict no-op — no protocol, no block, no event);
- workspace present, no memory content ⇒ protocol preamble only (the agent knows
  it can record, but there's nothing to recall yet);
- workspace present, with content ⇒ protocol preamble + the composed memory block.

The protocol is identity-level (D-B5), always present on agent turns for a real
persona, not router-gated. It rides the chokepoint with the memory block.

Protocol text (ported from OpenClaw's `AGENTS.default`), injected as the memory
block's preamble:

> You have a persistent memory at `<absolute workspace memory paths>`. On session
> start you were given today's + yesterday's notes and `MEMORY.md` above. To
> record something worth remembering: **read the file first** (`cat`), then
> **append** a concrete entry (`>>`). Write only real updates — decisions,
> preferences, constraints, open loops. **Never** write empty placeholders or
> "TODO: remember things". Durable facts go in `MEMORY.md`; today's working notes
> go in `memory/<today>.md`. You may re-read any memory file at any time.

The agent writes with ordinary shell (`cat path` to read, `echo "..." >> path`).
No new binary, no engine interception. The protocol travels WITH the memory block
through the chokepoint, so it reaches both the agent path (where shell writes
happen) and — harmlessly — the chat path.

**Why prompt-driven, not a CLI helper or magic command:** matches the Phase-3
decision that behavior emerges from injected text, not code branches (D3). The
agent already has the one tool it needs (bash). Robustness (dedup, formatting) is
the agent's responsibility per the protocol, exactly as a human editing a notes
file — and a malformed memory file degrades gracefully on the next read (blank/
trim rules), never aborting a turn.

---

## 6. Telemetry + the no-op guarantee

- A `memory_load` `_meta` event, **gated** exactly like `persona_load`: emitted
  only when `memory_load.injected` is non-empty, **after** `task_classified`, only
  on personalized turns (chat/agent, not clarify/ambiguous), **once per session**
  (a `memory_load_emitted` flag on `SessionState`). The empty case emits nothing.
- **The no-op guarantee (load-bearing, inherited from Phase A):** an **absent**
  workspace (no `~/.config/harness/agents/<id>/` dir) is **byte-identical** to
  today — no memory block, no protocol text, no `memory_load` event, no
  system-message change. Locked by a test. (A *present* workspace with empty
  memory deliberately injects the protocol preamble per §5 — that is intended new
  behavior for a real persona, not a no-op violation. The `memory_load` _event_ is
  still gated on `injected` being non-empty, so an empty-memory persona emits no
  event even though it gets the protocol text.)
- The order in the gated-emit block: `task_classified` → `persona_load` →
  `memory_load` → `skill_load` (memory after persona, before skills — matching the
  injection order).

---

## 7. Files touched

| File | Change |
|---|---|
| `harness/memory.py` | **new** — `MemoryLoad`, `resolve_memory(workspace_dir, *, today)`, the protocol-preamble constant |
| `harness/persona.py` | promote `_meaningful` + `_trim` to importable helpers (memory reuses them); no behavior change |
| `harness/acp_session.py` | `SessionState`: add `workspace_dir`, `memory_block`, `memory_load`, `memory_load_emitted`; `SessionStore.new(cwd, workspace_dir)` |
| `harness/acp_agent.py` | resolve memory once per session (from `state.workspace_dir`); gated `memory_load` emit; thread `memory_block` through `compose_context` + `ChatHandler`; `new_session` records the workspace per session |
| `harness/persona.py` `compose_context` / `TurnContext` | add `memory_block` param + field |
| `harness/tracing_agent.py` | append `memory_block` to the system template (base → persona → memory → skills) |
| `harness/chat_handler.py` | append `memory_block` to the persona system message |
| `harness/run_traced.py` | resolve memory for the default workspace; thread through |
| `tests/` | memory-compose unit tests; isolation (two sessions, different workspaces, isolated memory); injection-reach (both paths); gated-event; **no-op regression**; protocol-preamble presence |

---

## 8. Out of scope (later phases)

- **Selection UX** (`--persona`, `/persona`, `persona.toml`) — Phase C.
- **Memory summarization / compaction** when `MEMORY.md` outgrows the token budget
  — future (roadmap §6). B uses the same trim-at-inject ceiling as persona.
- **Cross-persona shared memory** — default is isolated.
- **Intra-session memory refresh** — first-turn-only by design (D-B4); the agent
  self-serves via `cat`.
- **Auto-capture / user `/remember`** — rejected in favor of agent-driven writes
  (D-B3).

---

## 9. Success criteria

1. `resolve_memory` reads MEMORY + today + yesterday, trims, skips blank/comment-
   only, never raises — unit tests; `today` is injected (no ambient clock).
2. Memory reaches **both** dispatch paths (agent system template + chat system
   message) — injection-reach test.
3. **Isolation:** two sessions with different workspace dirs get isolated memory
   (and persona) — isolation test. Proves the per-session plumbing.
4. The memory **write protocol** is present in the injected block when a persona
   has memory — preamble test.
5. **No-op guarantee:** an **absent** workspace is byte-identical to today,
   including **no `memory_load` event** — no-op regression test. (A present-but-
   empty workspace gets only the protocol preamble and still emits no event.)
6. The gated `memory_load` event fires once per session, after `task_classified`,
   only on personalized turns — gated-event test.
7. `.venv/bin/python -m pytest tests/ -q` green; Phase A persona tests unchanged.
8. NO selection UI / `persona.toml` / compaction introduced (scope held).
