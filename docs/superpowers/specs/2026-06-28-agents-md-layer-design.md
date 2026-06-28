# Design spec: AGENTS.md instruction layer (three-tier, read-only) — issue #47

**Date:** 2026-06-28
**Issue:** #47 (Multi-agent: AGENTS.md instruction layer — per-persona + project + global)
**Status:** Design — for review, then implementation plan.
**Scope:** Step 1 of the habits-as-learning roadmap. Read-only injection only. The
promotion/write path is #86; the memory-vs-AGENTS vocabulary audit is #85.

## Goal

Inject an **AGENTS.md instruction layer** into the agent's system prompt, composing
**three scopes** in a defined precedence:

1. **Persona** — `AGENTS.md` in the persona workspace dir (the operator's ops manual).
2. **Project** — `AGENTS.md` in the project working dir (the git/Claude-Code convention).
3. **Global** — `~/.config/harness/AGENTS.md` (applies to every persona/project).

It generalizes the existing content-layer pattern (persona/memory/skills): content-gated,
trim-capped, and resolved at the single `compose_context` chokepoint so every dispatch
path inherits it structurally. A strict **no-op** when no AGENTS.md files exist.

## Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Precedence | **persona > project > global** | The persona is the operator; its standing instructions are authoritative even inside a project. (User, 2026-06-28; resolves #47 Q1.) |
| Architecture | **One resolver, rides the chokepoint via `TurnContext`** | Robustness: a future dispatch path inherits AGENTS.md structurally and cannot ship AGENTS.md-blind. Avoids the 5-site threading the parked branch used and the chokepoint decision forbids. (Resolves #47 Q2.) |
| Project file resolution | **single `AGENTS.md` at cwd, no upward walk** | The harness never runs from a subdir; nothing in the codebase walks up for config. (#47 Q3, already investigated.) |
| Build scope | **read-only, all three tiers, one coherent feature** | User decision 2026-06-27; do NOT ship the cwd-only quick cut (parked `agents-md-inject` @ eee1105). |

## Non-goals (YAGNI)

- The promotion/write path ("habit recurs → becomes policy") — that is #86, decision-first.
- Memory-vs-AGENTS vocabulary cleanup — #85.
- Upward directory walking to find a repo-root AGENTS.md — #47 Q3 resolved against it.
- Merging/deduping instruction *content* across tiers — tiers are concatenated in
  precedence order with scope headers; the model reconciles, exactly as the three
  persona files already work.

## Architecture

A new resolver `harness/agents.py` mirrors `harness/memory.py` exactly (the proven
content-gated pattern): read each scope's `AGENTS.md`, gate on `_meaningful`, trim-cap,
never raise, track `skipped`. It returns ONE composed block with scope headers in
precedence order. The block joins `TurnContext` and rides the chokepoint.

### Precedence and prompt order

Precedence = **persona > project > global**. In the system prompt, later text carries
more weight (it is closest to the task), so the block is ordered **lowest-precedence
first**:

```
# Instructions (global)
<global AGENTS.md>

# Instructions (project)
<project AGENTS.md>

# Instructions (persona)
<persona AGENTS.md>
```

The composed `agents_block` is injected in `tracing_agent._render_template` AFTER
`base_block` and BEFORE `persona_block`/`memory_block`/`skill_block`. Final assembly:

```
base (policy + env + skills_menu)
  + agents_block        ← NEW (global → project → persona)
  + persona_block
  + memory_block
  + skill_block
```

Rationale for slot: AGENTS.md is standing *policy* (like the base), so it sits adjacent
to the base block, above the per-turn persona/memory/skills context. Within `agents_block`
the persona tier is last (highest precedence) — consistent with the locked precedence.

### Components

**`harness/agents.py`** (new, ~mirrors `memory.py`):

```python
AGENTS_FILE = "AGENTS.md"
MAX_AGENTS_CHARS = 8000          # per-tier trim cap (match memory's order of magnitude)

@dataclass
class AgentsLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)   # scope labels read
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (label, reason)

def _read_tier(path: Path, label: str, load: AgentsLoad) -> str | None:
    """Read one AGENTS.md; return '# Instructions (<label>)\\n<body>' or None when
    missing/blank/inert/unreadable. Reuses persona._meaningful / _trim. Never raises."""

def resolve_agents(*, persona_dir: Path | None, project_cwd: Path | None,
                   global_dir: Path | None) -> AgentsLoad:
    """Compose global+project+persona AGENTS.md, content-gated, ordered
    lowest-precedence-first (global, project, persona). Any None/absent/blank tier
    is skipped. No tier present => empty AgentsLoad (no block) — the no-op."""
```

- `_meaningful` and `_trim` are imported from `persona` (same as `memory.py` does) — DRY.
- Global dir = `paths.config_dir()` (where `done.conf`/skills already live).

**`harness/persona.py`** — `TurnContext` gains `agents_block: str = ""`; `compose_context`
gains `persona_dir`, `project_cwd`, `global_dir` params, calls `resolve_agents`, and puts
the result on the returned `TurnContext`. (Persona/memory blocks stay caller-resolved as
today; the AGENTS resolver is folded into the chokepoint output so all paths inherit it.)

**`harness/tracing_agent.py`** — `__init__` gains `agents_block: str = ""`;
`_render_template` injects it in the slot shown above (after base, before persona).

**Dispatch sites** (`run_traced.py`, `acp_agent.py`) — pass `persona_dir` (the workspace),
`project_cwd` (`state.cwd` / `args.cwd`), and `global_dir` (`paths.config_dir()`) into
`compose_context`, and pass `ctx.agents_block` into the runner / `_run_agent_turn`. No new
threading beyond the existing chokepoint params.

### Data flow

```
dispatch (per turn)
  │  persona_dir = workspace_dir
  │  project_cwd = state.cwd / args.cwd
  │  global_dir  = paths.config_dir()
  ▼
compose_context(... persona_dir, project_cwd, global_dir)
  │   └─ resolve_agents() → AgentsLoad(block, injected, skipped)
  ▼
TurnContext.agents_block
  ▼
runner → TracingAgent(agents_block=...) → _render_template injects after base
```

## Error handling

- Every file read is wrapped (mirror `_read_section`): `FileNotFoundError` → silently
  absent; other `OSError`/`UnicodeDecodeError` → recorded in `skipped`, never raises.
- Blank/inert (HTML-comment-only, whitespace) → `_meaningful` is False → skipped (the
  same gate that gives the templated-but-empty no-op).
- Over-cap content → trimmed with a `…[truncated]…` marker (mirror memory).
- A turn never fails because of AGENTS.md; worst case the block is empty.

## The no-op guarantee

With no `AGENTS.md` in any of the three locations:
- `resolve_agents` returns `AgentsLoad()` with empty `block`.
- `_render_template` appends `""` → system prompt byte-identical to today.
- `compose_context`'s new params default to `None` → callers that don't pass them are
  unaffected. Existing tests pass unchanged.

This repo's own root `AGENTS.md` (the operating standards) WILL be picked up as the
**project** tier when the agent runs here — an intentional, correct behavior change for
this repo, gated behind the file's existence everywhere else.

## Testing strategy

`tests/test_agents.py` (new):
- `resolve_agents` with: none present (empty no-op); one tier each; all three (order =
  global, project, persona in the block); blank/HTML-comment-only tier skipped;
  unreadable tier → `skipped`, no raise; over-cap tier trimmed.
- Precedence/order assertion: persona text appears AFTER project AFTER global in the block.
- Scope headers present and correct.

Integration:
- `compose_context` returns `agents_block` on `TurnContext`; defaults to "" when dirs are None.
- `_render_template` injects `agents_block` in the right slot; byte-identical when empty.
- Dispatch (run_traced + acp_agent) threads the three dirs; an `AGENTS.md` in a temp
  project cwd shows up in the agent's prompt; absent => unchanged.
- Full suite green (`.venv/bin/python -m pytest tests/ -q`), no regression to the 731 baseline.

## Risks & mitigations

- **Chokepoint signature widening** (`compose_context` gains 3 params): the deliberate,
  one-time cost of Option 1; defaults keep it backward-compatible.
- **Double-injection of this repo's AGENTS.md**: only the project tier reads cwd; persona
  and global read different dirs. No overlap unless a user literally symlinks them.
- **Precedence drift**: document the order next to `_render_template` and assert it in a
  test, so the implicit becomes explicit (the issue's standing complaint).
- **Parked branch confusion**: do NOT reuse `agents-md-inject` (eee1105) — it is cwd-only
  and threads 5 sites. Start from this spec.

## Rollout

Single shippable PR: `agents.py` + chokepoint wiring + injection slot + tests + a short
docs section in `docs/router-flows.md` (or a sibling `docs/agents-md.md`) explaining the
three tiers, precedence, and the no-op. Steps 2 (#85) and 3 (#86) follow as separate work.
