# Memory recall: typed manifest + `load_memory` tool — Design

**Status:** Locked (2026-06-28)
**Branch / worktree:** `memory-recall` (`.worktrees/memory-recall`)
**Owner:** Alberto
**Closes / advances:** #63 (recall index vs write-protocol — decision: index), #85 (memory vocabulary — facts get typed frontmatter)

---

## 1. Problem

Done's memory (`harness/memory.py`) today implements OpenClaw's "Architecture A":
per-persona workspace memory (`MEMORY.md` + today/yesterday daily notes),
content-gated startup injection, and a shell **write-protocol** preamble. It works,
but it has exactly one recall mode: **dump the whole file at startup**. There is:

- **No recall beyond startup** — the agent cannot pull a fact it didn't get in the
  opening dump. Daily notes older than yesterday are unreachable. As `MEMORY.md`
  grows it is silently trimmed at 8000 chars, dropping facts with no recourse.
- **No fact typing** — every line is undifferentiated prose; there is no
  user/feedback/project/reference vocabulary (the #85 gap).

A research spike (`wf_e883bf60-064`, 9 agents, 5 claims adversarially verified)
evaluated adopting **QMD** (OpenClaw's hybrid-search sidecar). Verdict: QMD is good
tech but a structural mismatch for Done — it is a **Node/Bun** sidecar (not a
Python library), pulls **~2GB of GGUF models** on first semantic query, needs a
PATH/lifecycle/scheduler, and breaks Done's byte-identical no-op invariant. The
spike's lead recommendation: **adopt QMD's *architecture* in-house, not its
binary** — because Done already shipped the exact pattern QMD's value reduces to
(the skills system: a startup **menu** + a `load_skill` **tool** that pulls bodies
on demand via the execute→observation path, with **zero new infra**).

## 2. Goal

Give memory a **second recall mode — load-on-demand — mirroring the skills
system**, and add **typed frontmatter** so facts are categorized. Specifically:

1. `MEMORY.md` can be (optionally) authored as a **typed manifest**: a list of
   one-line pointers to per-fact files, each with a category.
2. A **`load_memory` tool** lets the agent pull any memory file's full body into
   context on demand — the same `execute → observation` path as `load_skill`,
   with the same per-turn dedup.
3. **Fact typing** via YAML frontmatter (`name` / `description` / `type` where
   `type ∈ {user, feedback, project, reference}`), surfaced in the manifest.

Non-goals (explicitly deferred): no search index (FTS5), no QMD, no embeddings, no
auto-capture hook, no distillation/compaction loop, no cross-persona/global memory.
Each can be added later as an **additive** layer — files stay source of truth.

## 3. Guiding constraints (Done's values — every design choice ties to these)

- **Robust-minimal:** no new runtime dependency. `load_memory` is pure Python over
  the existing tool path. Stdlib only.
- **Byte-identical no-op when unused:** an empty/absent workspace, or a `MEMORY.md`
  with no manifest, behaves **exactly as today**. The tool is only registered when
  memory roots are passed (mirrors `build_registry(skill_roots=...)`).
- **Per-persona isolation:** recall is scoped to the session's `workspace_dir` — the
  same chokepoint memory and persona already resolve from. No cross-persona bleed.
- **Backward compatible:** existing plain-prose `MEMORY.md` files keep working
  unchanged (the manifest is *optional* and *additive*; prose without frontmatter
  is still injected at startup exactly as before).
- **Reuse the proven seam:** structurally clone `harness/skills.py` +
  `harness/tools/load_skill.py`. The regression surface is the *delta* from a
  tested, shipped path, not a new subsystem.

## 4. Architecture

Memory gets a **two-layer disclosure model identical to skills**:

| Layer | Skills (shipped) | Memory (this design) |
|---|---|---|
| Startup menu/manifest | `# Skills` menu (name + desc) | `MEMORY.md` (prose, OR a typed manifest of pointers) |
| Load-on-demand tool | `load_skill(skill_name)` | `load_memory(memory_name)` |
| Per-turn dedup | `env._loaded_skills` | `env._loaded_memories` |
| Resolution roots | `skills_dirs(project_cwd)` | the session `workspace_dir` (+ its `memory/` dir) |
| Content-gating | catalog empty ⇒ no menu, no tool wiring change | workspace empty ⇒ no block, tool not registered |

### 4.1 What a typed memory file looks like

A per-fact file under the workspace (e.g. `<workspace>/memory/user-terse.md`):

```markdown
---
name: user-terse
description: No trailing summaries after code changes
type: feedback
---

The user prefers terse responses. Skip the "here's what I did" recap after edits;
state the result in one line. Applies to all code-change turns.
```

- **Required:** `name` (lowercase a-z/0-9/hyphens, must match the filename stem),
  `description` (1-line, used in the manifest and to decide relevance).
- **`type`:** one of `user | feedback | project | reference`. Optional; defaults to
  `reference` if absent. Unknown values are kept verbatim but flagged as skipped-info
  (not fatal — mirrors skills' forward-compat).
- **Body:** the fact itself. No length cap on the *file* (only the startup-injected
  block is trimmed; loaded-on-demand bodies are capped at `MAX_MEMORY_CHARS` like a
  loaded skill body to bound a single observation).

### 4.2 What `MEMORY.md` becomes (optional manifest)

`MEMORY.md` may now be authored as a manifest — one line per fact file:

```markdown
# Memory

- [user-terse](memory/user-terse.md) — type:feedback — no trailing summaries
- [pr-workflow](memory/pr-workflow.md) — type:project — ship via PR, never main
```

This is **just markdown** — it is injected at startup exactly as today (the
manifest IS the menu). The `load_memory` tool reads the per-fact files the manifest
points to. **Crucially:** a `MEMORY.md` that is plain prose (no manifest) still works
— it is injected as-is, and `load_memory` can still pull any file under `memory/`.
The manifest is a *convention for keeping the startup block small*, not a requirement.

### 4.3 Resolution & roots

- `load_memory` resolves names against the session's **`workspace_dir`** and its
  `memory/` subdir. A name maps to `<workspace>/memory/<name>.md`, falling back to
  `<workspace>/<name>.md` (so `MEMORY.md` itself is loadable by name `MEMORY`).
- The tool is registered **only when a memory root is passed** to `build_registry`,
  exactly like `skill_roots`. No workspace ⇒ no `load_memory` tool ⇒ byte-identical.

## 5. Components (file structure)

### New / modified files

| File | Change | Responsibility |
|---|---|---|
| `harness/memory.py` | **modify** | Add `MemoryMeta` (name/description/type), `_meta_from_frontmatter`, `load_manifest(workspace)` (parse typed files under `memory/`), and `compose_memory(workspace, names)` (read bodies on demand, trimmed). `resolve_memory` (startup inject) is **unchanged in behavior**. |
| `harness/tools/load_memory.py` | **create** | `LoadMemoryTool(workspace_dir)` — structural clone of `LoadSkillTool`. `execute()` returns `{output, returncode, exception_info}`; per-turn dedup on `env._loaded_memories` with a `_fallback_loaded` for unstamped envs. |
| `harness/tools/registry.py` | **modify** | `build_registry(skill_roots=None, memory_root=None)` — append `LoadMemoryTool(memory_root)` when `memory_root` is given. Strict no-op when `None`. |
| `harness/tracing_agent.py` | **modify** | Reset `env._loaded_memories = set()` alongside `_loaded_skills` at turn start (line ~92). |
| `harness/acp_main.py`, `harness/run_traced.py` | **modify** | Thread the session/persona `workspace_dir` into `build_registry(..., memory_root=workspace_dir)`. |
| `harness/acp_agent.py` | **modify** | Where the per-session registry/model is built, pass `memory_root=state.workspace_dir` (the same per-session workspace persona/memory already use). Emit a `memory_load` meta event when a `load_memory` runs (optional, mirrors `skill_load`). |
| `tests/test_memory.py` | **modify** | Add manifest/typing/compose tests. |
| `tests/test_load_memory_tool.py` | **create** | Tool behavior: load hit, unknown name, dedup, isolation, no-op when no root. |
| `docs/memory.md` | **create** | User + agent docs: file format, typing, manifest convention, `load_memory`, per-persona isolation, deferred (no search/QMD) and why. |
| `README.md` | **modify** | Add a "Memory" subsection mirroring the "Skills" one. |

### Public interfaces (signatures other tasks rely on)

```python
# harness/memory.py
@dataclass
class MemoryMeta:
    name: str
    description: str
    type: str = "reference"          # user | feedback | project | reference

def load_manifest(workspace: Path) -> list[MemoryMeta]:
    """Parse typed per-fact files under <workspace>/memory/*.md. Skips blank/
    malformed; never raises. Empty list when none."""

def compose_memory(workspace: Path, names: list[str]) -> MemoryLoad:
    """Read the named memory files' bodies (trimmed at MAX_MEMORY_CHARS each) into
    one block. Mirrors skills.compose: .injected lists what was read, .skipped lists
    (name, reason). Missing/blank/unreadable => recorded in .skipped, not raised."""
```

```python
# harness/tools/load_memory.py
class LoadMemoryTool:
    name = "load_memory"
    def __init__(self, workspace_dir: Path): ...
    def execute(self, args: dict, env) -> dict:  # {"output","returncode","exception_info"}
```

```python
# harness/tools/registry.py
def build_registry(skill_roots: list[Path] | None = None,
                   memory_root: Path | None = None) -> list[Tool]: ...
```

## 6. Data flow

**Startup (unchanged):** `resolve_memory(workspace_dir, today)` injects
`MEMORY.md` + today/yesterday — content-gated, trimmed. If `MEMORY.md` is a
manifest, the manifest text IS the menu the agent sees.

**On-demand recall (new):**
1. Agent reads the manifest in its context, decides it needs `pr-workflow`.
2. Agent calls `load_memory(memory_name="pr-workflow")`.
3. `LoadMemoryTool.execute` checks `env._loaded_memories` (per-turn dedup); if new,
   calls `compose_memory(workspace, ["pr-workflow"])`, reads
   `<workspace>/memory/pr-workflow.md`, returns the trimmed body as the observation.
4. Engine routes the dict through `format_observation_messages` — same as `load_skill`.
5. Next turn, `tracing_agent` resets `_loaded_memories`, so the agent can re-pull.

## 7. Error handling

- **Unknown name:** return `{returncode: 1, output: "Unknown memory '<n>'. Available: <manifest names>"}`. Never raise (mirrors `load_skill`).
- **Unreadable / non-UTF8 / blank file:** recorded in `MemoryLoad.skipped`, surfaced
  in the tool output as a skip reason; the turn never fails.
- **Malformed frontmatter:** the file is still loadable by name; in the manifest it
  is listed with whatever `name`/`description` parsed, missing fields defaulted; a
  bad `type` is kept verbatim and noted. Forward-compatible, never fatal.
- **No workspace / no `memory/` dir:** `load_manifest` → `[]`; tool not registered;
  byte-identical no-op.
- **Path traversal guard:** `memory_name` is sanitized — reject names containing
  `/`, `..`, or absolute paths; resolve strictly within the workspace. (A loaded
  fact must never escape the persona's workspace — this is the cross-persona-bleed
  defense.)

## 8. Testing strategy

- **`resolve_memory` invariants preserved** — re-run all existing `test_memory.py`
  tests unchanged (no-op, content-gating, trimming, isolation, non-utf8). These are
  the regression guard: startup behavior must not move.
- **Manifest parsing** — typed file → `MemoryMeta`; default `type=reference`; bad
  `type` kept; blank/comment-only skipped; name-must-match-stem.
- **`compose_memory`** — hit reads body trimmed; missing → skipped not raised;
  multiple names; non-utf8 skipped.
- **`LoadMemoryTool`** — load hit returns body; unknown name returns rc=1 + available
  list; per-turn dedup (second call says "already loaded"); fresh set per turn;
  path-traversal name (`../x`, `/etc/passwd`) rejected; isolation (two workspaces).
- **Registry** — `memory_root=None` ⇒ exactly the 4 default tools (no `load_memory`);
  `memory_root=<dir>` ⇒ tool present.
- **Wiring smoke** — `build_registry` called with a per-session workspace produces a
  tool whose `execute` reads that workspace's files (no global state shared across
  two registries built for different personas).

Run from the worktree root: `.venv/bin/python -m pytest tests/ -q -p no:randomly`.
Baseline before changes: **770 passed**.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Regression in startup injection | `resolve_memory` is untouched in behavior; existing tests are the guard. New code is additive. |
| Cross-persona bleed via `load_memory` | Path-traversal sanitization + resolve strictly under `workspace_dir`; per-session tool construction (no shared mutable state). |
| Tool registered when it shouldn't be (breaks no-op) | `memory_root` defaults to `None`; tool only appended when a root is passed; mirrors the proven `skill_roots` gate. |
| Manifest convention ignored by users | Fully optional — plain-prose `MEMORY.md` still works; `load_memory` works over any `memory/*.md` regardless of manifest. |
| Scope creep into search/QMD | Explicit non-goals; FTS5/QMD are additive future layers, files stay source of truth. |

## 10. Open questions (none blocking — defaults chosen)

1. **Default `type` when absent** → `reference` (least-privileged category). *Decided.*
2. **Where per-fact files live** → `<workspace>/memory/<name>.md` (same dir as daily
   notes; daily notes are `YYYY-MM-DD.md`, facts are slugs — no collision). *Decided.*
3. **Emit a `memory_load` ACP meta event?** → Yes, mirror `skill_load` for trace
   parity; cheap and consistent. *Decided.*

---

## Appendix: why not QMD (spike summary)

QMD verified facts (5-claim adversarial pass): local embeddings / no leak ✓; files
source-of-truth, index disposable ✓; OpenClaw degrades gracefully without it ✓;
~2GB GGUF first-run download ✓; Node/Bun package, embeddable only in JS/TS (Done
must run it as a sidecar) ✓. Net: QMD violates robust-minimal, no-op-default,
portability, and Python-only to deliver *semantic* recall Done has no evidence it
needs. The in-house manifest+`load_memory` buys ~80% of the recall value at ~0% of
the infra cost, and leaves QMD as a clean optional Option-A backend for later **if**
cross-corpus semantic search is ever provably needed. Spike run: `wf_e883bf60-064`.
