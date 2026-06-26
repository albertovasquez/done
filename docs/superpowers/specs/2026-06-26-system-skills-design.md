# Phase A: System Skills (superpowers import) — Design

**Date:** 2026-06-26
**Status:** Approved (pending Codex review of this written spec)
**Branch / worktree:** `system-skills` (`.worktrees/system-skills`)

## Goal

Replace the 3 placeholder test skills with a curated set of **5 real
agent-behavior skills** imported from [obra/superpowers](https://github.com/obra/superpowers)
(MIT), bundled as system-provided skills the Router selects per request. This
turns the router→skills pipeline — the harness's differentiator — from a demo
into something that materially improves how the agent works.

This is **Phase A** of a 3-phase arc. Phase B (plugin system) and Phase C
(subagent dispatch) come later and will unlock the meta/orchestration skills
(subagent-driven-development, brainstorming, writing-plans,
dispatching-parallel-agents, using-superpowers) that assume that infrastructure.
Those are explicitly OUT of scope here.

## The 5 curated skills

Agent-behavior improvers, minimal infrastructure assumptions, self-contained:

| Skill | description (router selection hint) |
|---|---|
| `test-driven-development` | Use when implementing any feature or bugfix, before writing implementation code |
| `systematic-debugging` | Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes |
| `verification-before-completion` | Use before declaring work done / claiming success |
| `requesting-code-review` | Use when about to request review of a change |
| `receiving-code-review` | Use when responding to code-review feedback |

(Exact `description` strings are copied verbatim from upstream at import time.)

## Why these fit our loader as-is

Research against obra/superpowers @ `main` confirmed the format already matches
the harness contract:
- layout is `<name>/SKILL.md`; frontmatter has exactly `name` + `description`;
  `name` == directory name for all skills (our hard invariant holds);
- bodies are plain markdown; **zero `{{ }}`** so no Jinja collision (and the body
  bypasses Jinja anyway);
- `description` values are written as "Use when…" — ideal for the cheap router.

So the only per-file work is body cleanup (cross-reference stripping), not format
conversion.

## Components

This phase is **content + small test updates** — no new runtime code.

### A. The 5 skill files — `harness/skills/<name>/SKILL.md`

- Copy each SKILL.md from obra/superpowers at a **pinned commit** (record the SHA
  in NOTICE.md, §C).
- **Keep** frontmatter `name` + `description` verbatim.
- **Body cleanup (the only edit):**
  - Remove or rewrite `superpowers:<name>` references that point at skills NOT in
    the curated 5 (e.g. "use superpowers:writing-plans" → delete the sentence).
  - Rewrite references BETWEEN the 5 imported skills to a plain mention (e.g.
    "use superpowers:requesting-code-review" → "the code-review skill").
  - Remove markdown links to sibling files we don't bundle
    (`testing-anti-patterns.md`, `code-reviewer.md`, `root-cause-tracing.md`,
    etc.) — our loader injects ONLY the SKILL.md body, so such links are inert;
    delete the link text so the agent isn't told to read a file it can't.
  - **Leave** the `dot`/digraph flowchart blocks (harmless as prompt text;
    trimming is optional and deferred — YAGNI).
  - Edits are SURGICAL: remove dead pointers only; never rewrite the methodology.

### B. Remove the placeholder skills

Delete `harness/skills/git-pr-flow/`, `harness/skills/python-testing/`,
`harness/skills/poker-domain-rules/` (they were scaffolding for the skills layer,
never real content).

### C. Attribution — `harness/skills/NOTICE.md`

One file satisfying MIT redistribution: source repo URL, the pinned commit SHA,
the MIT license text, and the list of the 5 imported skills. Ships in the wheel
alongside the skills.

### D. Test updates (required — removed skills are referenced in tests)

- `tests/test_router.py`: fixtures use `poker-domain-rules` / `python-testing` in
  their catalogs → switch to real imported skill names (the router tests use stub
  JSON output, so they only need VALID catalog names to validate skill-filtering;
  point them at e.g. `systematic-debugging`).
- `tests/test_skills.py`: any fixtures/assertions referencing the removed skills
  → update. Add:
  - all 5 system skills appear in `load_catalog(skills_dirs())` with non-empty
    descriptions;
  - each composes to a non-empty body via `compose`;
  - the 3 removed skills are absent from the catalog.
- New assertion (catches a bad copy): every `harness/skills/*/SKILL.md` has
  frontmatter `name` equal to its directory name.

### E. README

Document the bundled system skills: what the 5 are, that the Router auto-selects
them per request, and that user skills in `~/.config/harness/skills/` override a
system skill by the same name. (Confirm `--yolo` is in the flags table while
there; add if missing.)

## Data flow (unchanged path, better catalog)

The existing Phase 2/3 path, now fed a real catalog:

```
prompt → Router.classify(prompt) with catalog = load_catalog(skills_dirs())
       → Classification(task_type, skills=[…selected from the 5…])
       → skills.compose(skills_dirs(), selected) → injectable block
       → TracingAgent injects block into system prompt (post-Jinja)
       → agent runs the task WITH the methodology in-context
```

- `skills_dirs()` (Phase 6) already returns `[bundled, ~/.config/harness/skills]`;
  user skills still override by name — UNCHANGED. We only changed what's bundled.
- Selection quality improves because the catalog is now real skills with sharp
  "Use when…" descriptions.
- `skill.load` event (skipped-and-shown for malformed skills) — unchanged.
- Mock mode unaffected: catalog is filesystem (no model); the router stub selects
  no skills.

## Error handling — existing robustness covers it

- A malformed imported SKILL.md (bad frontmatter, name≠dir) is **skipped-and-shown**
  via the `skill.load` event, never fatal (existing `skills.py`). A botched edit
  degrades to "that skill isn't selectable," not a crash.
- No Jinja risk (no `{{ }}`; body bypasses Jinja).
- The one real risk is a manual body edit garbling a skill's meaning →
  mitigated by surgical edits + reviewing each edited file (it IS the work).

## Testing

- `tests/test_skills.py` — catalog has all 5; each composes non-empty; removed 3
  absent; every shipped SKILL.md has name==dir.
- `tests/test_router.py` — fixtures point at real imported names; stub-driven
  skill-filtering still validates.
- No live-model router-quality test (selection is a model judgment, not
  unit-testable). A manual smoke covers it: run a debugging-flavored prompt with
  `--model vibeproxy`, observe the `systematic-debugging` chip.
- The Phase-6 packaging test already asserts `harness/skills/*/SKILL.md` ships in
  the wheel — it auto-covers the new skills.
- Full suite must stay green.

## Global Constraints

- **License compliance.** obra/superpowers is MIT; retain the license text +
  attribution (NOTICE.md) for redistribution. Do not bundle their `scripts/`
  (Node server, shell helpers) — skills text only.
- **Loader contract unchanged.** `name` == dir name for every shipped skill;
  body is injected verbatim; no Jinja in bodies.
- **Surgical body edits only** — strip dead cross-refs/links; never rewrite the
  methodology content.
- **No sync tooling** — manual one-time copy; updating later is a manual re-copy.
- **User-skill override preserved** — `~/.config/harness/skills/<name>` still wins
  over a bundled system skill of the same name (Phase 6 `skills_dirs` ordering).

## Out of scope (later phases)

- The meta/orchestration skills (need Phase B plugin system + Phase C subagents).
- A sync/update mechanism (manual copy for now).
- Trimming `dot` flowchart blocks or long bodies from skill text.
- Any change to the Router/loader runtime code (catalog content only).
