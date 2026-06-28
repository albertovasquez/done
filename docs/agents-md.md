# AGENTS.md instruction layer

The harness reads `AGENTS.md` files and injects them as **standing instructions** into
the agent's system prompt. It composes **three scopes** so a global default, a project's
house rules, and a persona's ops manual can all apply at once.

## The three tiers

| Tier | Location | Meaning |
|------|----------|---------|
| **Persona** | the persona workspace dir (`~/.config/harness/agents/<persona>/AGENTS.md`) | the operator's ops manual — how *this persona* works |
| **Project** | the launch working directory (`<cwd>/AGENTS.md`) | the house rules of the repo you're working in (the git / Claude-Code convention) |
| **Global** | `~/.config/harness/AGENTS.md` | defaults applied to every persona and project |

Each tier is optional. A tier with no `AGENTS.md` (or a blank / HTML-comment-only one) is
simply skipped.

## Precedence

**persona > project > global.** The persona is the operator, so its standing instructions
are authoritative even inside a project; project house rules outrank global defaults.

Precedence is enforced two ways, so it does not rely on prompt position alone:

1. A **preamble** in the injected block states it in words: *"When they conflict, follow
   persona over project over global."*
2. The tiers are **ordered lowest-precedence-first** (global → project → persona), so the
   highest-precedence tier sits last, closest to the task.

The injected block looks like:

```
# Instructions

Standing instructions for this session. When they conflict, follow persona over project over global.

## Global instructions
<global AGENTS.md body>

## Project instructions
<project AGENTS.md body>

## Persona instructions
<persona AGENTS.md body>
```

## How it's wired

`harness/agents.py` `resolve_agents(persona_dir, project_cwd, global_dir)` reads and
composes the tiers (content-gated, trim-capped at 8000 chars/tier, never raises). The
dispatch layer (`run_traced.py`, `acp_agent.py`) resolves it where it builds `base_block`
and passes it to `render_base_prompt(agents_block=...)`.

Because `base_block` is consumed by **both** the agent runner and the chat handler,
AGENTS.md reaches **both** the work path and the chat path — it is policy, not per-turn
context, so it lives in the base block rather than the per-turn `compose_context`.

The content-gate helpers (`_meaningful`, `_trim`) live in the leaf module
`harness/textgate.py`, shared by `persona.py`, `memory.py`, and `agents.py` (this keeps
`agents.py` cycle-free).

## The no-op guarantee

With no `AGENTS.md` in any of the three locations, `resolve_agents` returns an empty
block, `render_base_prompt(agents_block="")` is byte-identical to before, and the system
prompt is unchanged. AGENTS.md is purely additive: you get it only by creating the file.

## Known limitation

The **project** tier reads `AGENTS.md` at the **launch working directory only** — there is
no upward directory walk. `--cwd` is not enforced to a repo root, so launching the agent
from a subdirectory will miss a repo-root `AGENTS.md`. Run from the repo root, or place an
`AGENTS.md` at your working directory. (Upward-walk is a possible future enhancement.)

## Editing

`AGENTS.md` is re-read each turn, so edits take effect on the next turn — no restart
needed. This repo's own root `AGENTS.md` (the operating standards) is picked up as the
**project** tier when the agent runs from the repo root.

See also: `docs/router-flows.md` (the skills/flows layer that composes alongside this).
