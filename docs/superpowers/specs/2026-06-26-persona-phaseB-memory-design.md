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
    block: str = ""                                          # protocol preamble + composed files (empty unless content)
    injected: list[str] = field(default_factory=list)        # memory filenames composed in
    skipped: list[tuple[str, str]] = field(default_factory=list)

MEMORY_FILE = "MEMORY.md"
MEMORY_DIR = "memory"
MAX_MEMORY_CHARS = 8000          # per-file trim ceiling (reuse persona's value/helper)

def resolve_memory(workspace_dir: Path | None, *, today: date) -> MemoryLoad: ...
```

- Reads `MEMORY.md` (durable) + `memory/<today>.md` + `memory/<yesterday>.md`.
  **`today` is passed in, not `date.today()`** — testability + the codebase's
  no-ambient-clock discipline. The caller computes `today` ONCE per session at
  session-start in **local time** and that date is fixed for the session's memory
  (a long session crossing midnight keeps its session-start date; the next session
  recomputes — consistent with first-turn-only caching, §4). `yesterday = today -
  timedelta(days=1)`.
- Reuses `persona._meaningful` and `persona._trim` (promote them to shared helpers
  or import — see §7). Blank/comment-only files skip; oversized files trim with the
  marker. **Same discipline as `compose_persona`** — a bad file never aborts a turn.
- **CONTENT-GATED composition (resolves the no-op boundary — see §5):** the block
  (protocol preamble + files) is produced **only when at least one memory file has
  real content** (`injected` non-empty). Otherwise `block == ""`, exactly like
  `compose_persona` on empty input:
  - no content (absent workspace, OR present workspace with all files
    missing/blank/comment-only) ⇒ `MemoryLoad()` empty, `block == ""` — strict
    no-op. The seeded-but-unused default persona is byte-identical (preserves the
    Phase A guarantee + its existing test).
  - ≥1 file with content ⇒ `block ==` protocol preamble + composed file sections.
- Block shape when non-empty (labeled, parallel to `# Persona`):
  ```
  \n\n# Memory\n\n
  <protocol preamble — §5>\n\n
  ## MEMORY.md\n<durable body>\n\n
  ## memory/<today>\n<today body>\n\n
  ## memory/<yesterday>\n<yesterday body>
  ```
  Only the sections for files that HAVE content appear. The protocol preamble is
  part of this block, so it is present iff the block is (content-gated).

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

Chat path (`ChatHandler`) — **precise composition (#5):** `acp_agent` pre-concatenates
`state.persona_block + state.memory_block` into ONE identity string and passes it as
the existing `persona_block` param (no new ChatHandler param — keeps the change
minimal). `ChatHandler` already emits a system message iff that string is non-empty
(`chat_handler.py:81`), so:
- persona only → system message = persona;
- memory only (persona empty) → system message = memory (the concat is just the
  memory block, still non-empty → one system message is created);
- both → persona + memory in one system message;
- neither → no system message (byte-identical no-op).

So memory-only sessions DO get a system message. The concatenation happens in
`acp_agent`, not `ChatHandler` — `ChatHandler`'s contract is unchanged.

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
multi-persona selection logic. C adds the user-facing valve. This is the one part
of B that is "more than memory," and it is deliberately minimal.

**What the isolation test actually proves (#8 — honest about the layer):**
`HarnessAgent.new_session` has no workspace parameter (`acp_agent.py:77`) and in B
always records `self._workspace_dir` (the default), so the *public ACP flow* cannot
yet select a second workspace. The isolation test therefore proves the **plumbing
layer**, not a user flow: it constructs two `SessionState`s with different
`workspace_dir` values (via `SessionStore.new(cwd, workspace_dir)` directly, or by
setting `agent._workspace_dir` between `new_session` calls) and asserts their
resolved memory/persona blocks are independent. That is the genuine Phase-B
deliverable — the per-session pipe exists and is keyed correctly; Phase C wires a
selector to it.

---

## 5. The write side — the memory protocol (D3, prompt-driven)

The agent acts only through shell (`BASH_TOOL`). So the "memory-write tool" is a
**prompt-injected protocol**, not a new mechanism.

**Injection rule — CONTENT-GATED (#1, corrected after adversarial review):** the
protocol preamble + memory block inject **iff `resolve_memory` produced content**
(`injected` non-empty), exactly like persona/skills gate on theirs. This is the
load-bearing fix: the default install seeds the workspace (`acp_main.py:79` →
`seed_default_workspace`), so "inject when the workspace exists" would inject the
protocol on **every default install** and break the Phase A byte-identical-no-op
test (`test_acp_session_context.py:343`). Content-gating preserves it: a
seeded-but-unused default persona has no memory content → no block → no protocol →
byte-identical. A persona becomes memory-active once it *has* memory content. (How
a brand-new persona first learns it can keep memory is a documentation concern, not
engine injection — out of scope for B; the protocol appears as soon as any memory
file has content.)

Protocol text (ported from OpenClaw's `AGENTS.default`), the memory block's preamble,
with the session's **absolute, quoted** workspace paths interpolated (#3) and
safe shell patterns (#2 dir-create, #3 missing-file read):

> You have a persistent memory in this workspace. Its files were given to you above
> (when present). To record something worth remembering:
> 1. Ensure the dir exists: `mkdir -p "<abs-workspace>/memory"`
> 2. Read before writing: `test -f "<file>" && cat "<file>"`
> 3. Append a concrete entry: `printf '%s\n' "..." >> "<file>"`
>
> Write only real updates — decisions, preferences, constraints, open loops.
> **Never** write empty placeholders. Durable facts → `"<abs-workspace>/MEMORY.md"`;
> today's working notes → `"<abs-workspace>/memory/<today>.md"`. You may re-read any
> memory file at any time.

All paths are interpolated as **absolute and double-quoted** (the workspace is under
the XDG/home config dir — `paths.py` — and may contain spaces). The `mkdir -p` makes
the first daily write safe even though seeding never creates `memory/` (#2). The
`test -f && cat` makes "read before write" safe on a not-yet-existing file (#3).

**Permission (#9):** in ACP, a write command goes through `request_permission` like
any project mutation (`acp_env.py:31` → `acp_agent.py`). Phase B does **not**
special-case memory paths — a memory append is a normal command the user may be
prompted for (and may deny; the agent continues, the memory just isn't written).
Auto-allowing writes under `~/.config/harness/agents/` is a deliberate
non-goal for B (it would carve a permission exception, which the project routes to
Codex review); revisit if the prompt-on-every-write UX proves annoying. Stated, not
solved.

No new binary, no engine interception. The protocol travels WITH the memory block
through the chokepoint, so it reaches the agent path (where shell writes happen) and
— harmlessly — the chat path.

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
- **#4 capability-chat caveat:** a `chat_question` that is a capability question
  short-circuits in `ChatHandler` (`chat_handler.py:69`) BEFORE any model message
  is built — so on that sub-path memory isn't actually injected into a model, yet
  the gated emit (which fires in `acp_agent` before dispatch detail is known) would
  still report `memory_load`. The existing `persona_load`/`skill_load` have the
  same shape; `memory_load` is consistent with them. The event therefore means
  "memory was RESOLVED for this turn," not "a model saw it." We accept this
  (consistency with persona/skills telemetry) rather than special-casing
  capability-chat; the no-op (no memory content → no event) is unaffected.
- **The no-op guarantee (load-bearing, inherited from Phase A):** **no memory
  content** (absent workspace, OR present-but-empty/inert memory — incl. the
  seeded default) is **byte-identical** to today — no memory block, no protocol
  text, no `memory_load` event, no system-message change. Locked by a test that
  reuses the seeded-default scenario from `test_acp_session_context.py:323`.
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
| `harness/acp_agent.py` | resolve memory once per session (from `state.workspace_dir`, computing `today` at session start); gated `memory_load` emit; thread `memory_block` through `compose_context`; pre-concat `persona_block + memory_block` for the `ChatHandler` system message (#5); `new_session` records the workspace per session |
| `harness/persona.py` `compose_context` / `TurnContext` | add `memory_block` param + field |
| `harness/tracing_agent.py` | append `memory_block` to the system template (base → persona → memory → skills) |
| `harness/chat_handler.py` | **NO signature change** (#5) — `acp_agent` pre-concatenates persona+memory into the existing `persona_block` param |
| `harness/run_traced.py` | resolve memory for the default workspace; thread `memory_block` through `MiniSweAgentRunner.run` |
| `harness/runner.py` (#6) | `MiniSweAgentRunner.run` gains `memory_block: str = ""`, forwarded to `TracingAgent` alongside `skill_block`/`persona_block` (else memory can't reach the non-ACP dev path) |
| `tests/` | memory-compose unit tests (content-gating, today injected, trim/blank/inert); isolation (two `SessionState`s, different workspace_dir, isolated memory — via `SessionStore.new` directly, #8); injection-reach (both paths); gated-event; **no-op regression** (seeded default = byte-identical, reuses the Phase-A scenario); protocol-preamble presence when content exists |

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
