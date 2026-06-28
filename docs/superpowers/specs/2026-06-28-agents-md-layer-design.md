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
trim-capped, and resolved by the caller then folded into **`base_block`** (the policy
block both the agent AND chat paths already consume) so every dispatch path inherits it.
A strict **no-op** when no AGENTS.md files exist.

> **Revised after Codex rescue review (2026-06-28).** The first draft routed AGENTS.md
> through `compose_context`/`TurnContext`. Codex found two blockers: (a) `compose_context`
> calling `resolve_agents` creates an **import cycle** (`agents.py` needs `_meaningful`/
> `_trim` from `persona.py`, which would then import `agents.py`); (b) the **chat path
> bypasses `compose_context` entirely** (`acp_agent.py:380`, `run_traced.py:197`), so it
> would never see AGENTS.md. Both are fixed by resolving AGENTS.md in the caller and
> appending it to `base_block` via `render_base_prompt` — `base_block` is the true
> chokepoint for *policy* (both paths consume it), where `compose_context` is the
> chokepoint only for *per-turn context* (agent path). See "Architecture (revised)".

## Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Precedence | **persona > project > global** | The persona is the operator; its standing instructions are authoritative even inside a project. (User, 2026-06-28; resolves #47 Q1.) |
| Architecture | **Caller resolves; folded into `base_block` via `render_base_prompt`** | `base_block` is consumed by BOTH the agent and chat paths (verified), so AGENTS.md reaches both. Avoids the import cycle and the chat bypass that routing through `compose_context` would cause. (Resolves #47 Q2 + Codex blockers.) |
| Project file resolution | **single `AGENTS.md` at launch `cwd`, no upward walk** | An upward walk is scope creep; keep it simple. NOTE (Codex): `--cwd` is arbitrary and not enforced to repo-root, so a repo-root `AGENTS.md` *can* be missed if launched from a subdir — documented as a known limitation; upward-walk is a future enhancement. (#47 Q3.) |
| Gate helpers | **move `_meaningful`/`_trim` (+`_HTML_COMMENT`) to a leaf `harness/textgate.py`** | `agents.py`, `memory.py`, and `persona.py` all import them from the leaf — kills any import-cycle risk and is a clean DRY improvement. |
| Build scope | **read-only, all three tiers, one coherent feature** | User decision 2026-06-27; do NOT ship the cwd-only quick cut (parked `agents-md-inject` @ eee1105). |

## Non-goals (YAGNI)

- The promotion/write path ("habit recurs → becomes policy") — that is #86, decision-first.
- Memory-vs-AGENTS vocabulary cleanup — #85.
- Upward directory walking to find a repo-root AGENTS.md — #47 Q3 resolved against it.
- Merging/deduping instruction *content* across tiers — tiers are concatenated in
  precedence order with scope headers; the model reconciles, exactly as the three
  persona files already work.

## Architecture (revised)

A new resolver `harness/agents.py` mirrors `harness/memory.py`'s content-gated pattern:
read each scope's `AGENTS.md`, gate on `_meaningful`, trim-cap, never raise, track
`skipped`. It returns ONE composed block with scope headers in precedence order. The
caller resolves it and folds it into **`base_block`** — the policy block consumed by
**both** the agent path and the chat path — via `render_base_prompt`.

### Why `base_block`, not `compose_context` (the Codex-driven correction)

- `compose_context` is the chokepoint for **per-turn context** (persona/memory/skills) —
  but it is called **only on the agent path** (`acp_agent.py:409`). The chat path builds
  `ChatHandler` directly (`acp_agent.py:380`, `run_traced.py:197`) and never touches it.
- `base_block` (from `render_base_prompt`) is built once per dispatch and passed to
  **both** the runner AND `ChatHandler` (`run_traced.py:171/183/199`; `acp_agent.py:373`
  → both branches). It already carries policy + env + persona-files + skills-menu.
- AGENTS.md is standing **policy**, not per-turn context. So it belongs in `base_block`.
  This reaches chat for free and avoids the import cycle (`render_base_prompt` is pure —
  the caller resolves the file I/O and passes the string in).

### Precedence and prompt order

Precedence = **persona > project > global**. Two mechanisms enforce it, so we do NOT
rely on the unproven "later text = stronger" convention alone (Codex #4):

1. **Explicit scope headers + a precedence sentence** in the block text itself.
2. **Order**: tiers placed lowest-precedence-first, so the highest-precedence (persona)
   sits last/closest to the task as a secondary reinforcement.

```
# Instructions

(When these conflict, follow persona over project over global.)

## Global instructions
<global AGENTS.md>

## Project instructions
<project AGENTS.md>

## Persona instructions
<persona AGENTS.md>
```

`agents_block` is appended inside `render_base_prompt` AFTER the skills menu. Final
`base_block` order: `policy + env + persona-files + skills_menu + agents_block`. Both the
agent prompt and the chat system prompt then carry it (the agent prompt further appends
persona_block + memory_block + skill_block in `tracing_agent._render_template`, unchanged).

### Components

**`harness/textgate.py`** (new leaf) — holds `_meaningful`, `_trim`, and `_HTML_COMMENT`,
moved out of `persona.py`. `persona.py`, `memory.py`, and `agents.py` import them from
here. This kills the import-cycle risk (Codex #1) and is a clean DRY move. `persona.py`
and `memory.py` change only their import line; behavior is identical (assert via the
existing tests). Public names kept (or re-exported from `persona` for back-compat if any
external caller imports them — grep shows only `memory.py` + `persona.py` do).

**`harness/agents.py`** (new):

```python
AGENTS_FILE = "AGENTS.md"
MAX_AGENTS_CHARS = 8000          # per-tier trim cap (memory's order of magnitude)

@dataclass
class AgentsLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)            # scope labels read
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (label, reason)

def _read_tier(path: Path, label: str, load: AgentsLoad) -> str | None:
    """Read one AGENTS.md; return '## <label> instructions\\n<body>' or None when
    missing/blank/inert/unreadable. Uses textgate._meaningful / _trim. Never raises."""

def resolve_agents(*, persona_dir: Path | None, project_cwd: Path | None,
                   global_dir: Path | None) -> AgentsLoad:
    """Compose global+project+persona AGENTS.md, content-gated, lowest-precedence-first
    (global, project, persona), with a precedence preamble when any tier is present.
    Any None/absent/blank tier is skipped. No tier present => empty AgentsLoad (no
    block) — the no-op."""
```

- Imports `_meaningful`/`_trim` from `harness.textgate` (no `persona` dependency → no cycle).
- Global dir = `paths.config_dir()` (where `done.conf`/skills already live).

**`harness/base_prompt.py`** — `render_base_prompt` gains `agents_block: str | None = None`,
appended after `skills_menu`: `return BASE_POLICY + env + persona + (skills_menu or "") +
(agents_block or "")`. Pure; omit-when-None → byte-identical no-op.

**Dispatch sites** (`run_traced.py`, `acp_agent.py`) — where `base_block` is built, call
`agents.resolve_agents(persona_dir=workspace_dir, project_cwd=state.cwd/args.cwd,
global_dir=paths.config_dir())` and pass `agents_block=load.block` into
`render_base_prompt`. Because `base_block` already flows to both the runner and
`ChatHandler`, no further threading is needed and BOTH paths inherit AGENTS.md.

`compose_context`, `TurnContext`, and `tracing_agent` are **unchanged** — the first
draft's changes there are dropped (they caused the cycle + missed chat).

### Data flow

```
dispatch (per turn, where base_block is built)
  │  persona_dir = workspace_dir
  │  project_cwd = state.cwd / args.cwd
  │  global_dir  = paths.config_dir()
  ▼
agents.resolve_agents(persona_dir, project_cwd, global_dir) → AgentsLoad(block, injected, skipped)
  ▼
render_base_prompt(..., agents_block=load.block)  → base_block (now carries AGENTS.md)
  ├──► runner → TracingAgent(base_block=...)        (agent path)
  └──► ChatHandler(base_block=...)                  (chat path)   ← both inherit it
```

## Error handling

- Every file read is wrapped (mirror `memory._read_section`): `FileNotFoundError` →
  silently absent; other `OSError`/`UnicodeDecodeError` → recorded in `skipped`, never raises.
- Blank/inert (HTML-comment-only, whitespace) → `_meaningful` is False → skipped (the
  same gate that gives the templated-but-empty no-op).
- Over-cap content → trimmed with a `…[truncated]…` marker (mirror memory).
- A turn never fails because of AGENTS.md; worst case the block is empty.

## The no-op guarantee

With no `AGENTS.md` in any of the three locations:
- `resolve_agents` returns `AgentsLoad()` with empty `block`.
- `render_base_prompt(agents_block="")` → `base_block` byte-identical to today.
- `render_base_prompt`'s new param defaults to `None` → callers that don't pass it are
  unaffected. The `textgate` extraction is pure-move (same functions, same behavior).
  Existing tests pass unchanged (verified targets: `test_tracing_agent_skills.py:55-57`,
  `test_persona.py:113-115`, the base_prompt no-op tests).

This repo's own root `AGENTS.md` (the operating standards) WILL be picked up as the
**project** tier when the agent runs from this repo root — an intentional, correct
behavior change for this repo. **Watch-for (Codex):** any existing test that builds a
prompt from this repo's cwd and asserts exact content will now see the root AGENTS.md;
those tests must pass an explicit temp `project_cwd` (or `None`) to stay hermetic. The
plan must audit prompt-asserting tests for this.

## Known limitation (documented, not fixed here)

`AGENTS.md` is read at the **launch cwd** only — no upward directory walk (Codex #3).
`--cwd` is arbitrary and not enforced to repo-root (`tui_main.py:97`, `acp_main.py:105`,
`run_traced.py:124`), so launching from a subdirectory will miss a repo-root `AGENTS.md`.
Acceptable for v1; upward-walk is a future enhancement (note it in the docs).

## Testing strategy

`tests/test_agents.py` (new):
- `resolve_agents` with: none present (empty no-op); one tier each; all three (order in
  block = global, project, persona); blank/HTML-comment-only tier skipped; unreadable tier
  → `skipped`, no raise; over-cap tier trimmed.
- Precedence: persona text appears AFTER project AFTER global; the precedence preamble
  sentence is present when any tier exists; scope headers correct.

`tests/test_textgate.py` (new) — `_meaningful`/`_trim` behavior preserved post-move
(or fold into existing persona/memory tests if they already cover it).

`harness/base_prompt.py` tests — `render_base_prompt(agents_block=None)` is byte-identical
to no-arg; `agents_block="..."` appends after the skills menu.

Integration:
- Dispatch (run_traced + acp_agent): an `AGENTS.md` in a temp `project_cwd` shows up in
  BOTH the agent prompt AND the chat system prompt; absent => byte-identical.
- Audit existing prompt-asserting tests for the repo-root-AGENTS.md leak (pass temp cwd).
- Full suite green (`.venv/bin/python -m pytest tests/ -q`), no regression to the 731 baseline.

## Risks & mitigations

- **Import cycle** (Codex #1, blocker): broken by the `textgate` leaf module — `agents.py`
  imports gate helpers from `textgate`, never from `persona`; nothing imports `agents` at
  `persona` module scope. Asserted by a "no import cycle" smoke import in tests.
- **Chat-path coverage** (Codex #2): fixed by folding AGENTS.md into `base_block`, which
  both paths consume — verified at the call sites.
- **Repo-root AGENTS.md leaks into hermetic tests** (Codex #5): plan audits prompt-asserting
  tests and passes explicit temp/None `project_cwd`.
- **Precedence is convention, not guaranteed** (Codex #4): enforced by explicit headers +
  a precedence preamble sentence, not by prompt position alone; asserted in a test.
- **Parked branch confusion**: do NOT reuse `agents-md-inject` (eee1105) — cwd-only, threads
  5 sites. Start from this spec.

## Rollout

Single shippable PR: `textgate.py` (extract) + `agents.py` (resolver) + `render_base_prompt`
arg + dispatch wiring (2 sites) + tests + a `docs/agents-md.md` explaining the three tiers,
precedence, the no-op, and the launch-cwd limitation. Steps 2 (#85) and 3 (#86) follow.
