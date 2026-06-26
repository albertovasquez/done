# Phase A: System Skills (superpowers import) — Design

**Date:** 2026-06-26
**Status:** Approved, revised post-review. Ready for writing-plans.
**Branch / worktree:** `system-skills` (`.worktrees/system-skills`)

> **Revision note (2026-06-26):** Review (Codex spec pass + direct verification
> against the real upstream files) refined the curated set from 5 → **4 skills**:
> `requesting-code-review` was DROPPED because its core action is "dispatch a
> subagent to review" — infrastructure we don't have until Phase C. It will be
> imported then. Also confirmed: the loader's `split('---', 2)` handles bodies
> with markdown-table separators safely (verified against the real TDD skill);
> the exact cross-ref/link cleanups are now enumerated per skill below; and the
> only tests needing fixture updates are `test_router.py` and `test_run_traced.py`
> (`test_skills.py` uses synthetic tmp-dir skills and is unaffected).

## Goal

Replace the 3 placeholder test skills with a curated set of **4 real
agent-behavior skills** imported from [obra/superpowers](https://github.com/obra/superpowers)
(MIT), bundled as system-provided skills the Router selects per request. This
turns the router→skills pipeline — the harness's differentiator — from a demo
into something that materially improves how the agent works.

This is **Phase A** of a 3-phase arc. Phase B (plugin system) and Phase C
(subagent dispatch) come later and will unlock the meta/orchestration skills
(subagent-driven-development, brainstorming, writing-plans,
dispatching-parallel-agents, using-superpowers) that assume that infrastructure.
Those are explicitly OUT of scope here.

## The 4 curated skills

Agent-behavior improvers, infra-free, coherent when injected today:

| Skill | description (router selection hint) |
|---|---|
| `test-driven-development` | Use when implementing any feature or bugfix, before writing implementation code |
| `systematic-debugging` | Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes |
| `verification-before-completion` | Use before declaring work done / claiming success |
| `receiving-code-review` | Use when responding to code-review feedback |

(Exact `description` strings are copied verbatim from upstream at import time.)

**Dropped (→ Phase C):** `requesting-code-review` — its methodology is "dispatch a
subagent to review, filling code-reviewer.md," which needs subagent dispatch we
don't have yet. Stripping that to make it infra-free would mean rewriting its
methodology (against our surgical-edits rule), so it's deferred to Phase C.

### Per-skill cleanup (verified against the real upstream bodies)

- `test-driven-development`: strip the one link to `testing-anti-patterns.md`
  (sibling file, not bundled). Body otherwise verbatim.
- `systematic-debugging`: 3 `superpowers:` refs — all point at skills in OUR set
  (test-driven-development, verification-before-completion). De-namespace to plain
  mentions (e.g. "the test-driven-development skill"); no dangling refs to strip.
- `verification-before-completion`: clean — no refs or links.
- `receiving-code-review`: clean — no refs or links, no subagent assumptions.

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

### A. The 4 skill files — `harness/skills/<name>/SKILL.md`

- Copy each SKILL.md from obra/superpowers at a **pinned commit** (record the SHA
  in NOTICE.md, §C). Pin to a SHA, not `main`.
- **Keep** frontmatter `name` + `description` verbatim.
- **Body cleanup** — the precise, verified edits per skill (see "Per-skill
  cleanup" above for the enumerated list):
  - `test-driven-development`: strip the `testing-anti-patterns.md` link.
  - `systematic-debugging`: de-namespace the 3 `superpowers:` refs (all point at
    our own set) to plain mentions; nothing dangling.
  - `verification-before-completion`, `receiving-code-review`: no edits (clean).
  - **Leave** the `dot`/digraph flowchart blocks (harmless as prompt text;
    trimming is optional and deferred — YAGNI).
  - Edits are SURGICAL: remove dead pointers only; never rewrite the methodology.

### B. Remove the placeholder skills

Delete `harness/skills/git-pr-flow/`, `harness/skills/python-testing/`,
`harness/skills/poker-domain-rules/` (they were scaffolding for the skills layer,
never real content).

### C. Attribution — `harness/skills/NOTICE.md`

One file satisfying MIT redistribution: source repo URL, the pinned commit SHA,
the MIT license text, and the list of the 4 imported skills. Ships in the wheel
alongside the skills.

### D. Test updates (verified breakage list)

Grep confirmed exactly these reference the removed skills:
- `tests/test_router.py` (lines 9-10, 50, 55, 98): the `_CATALOG` fixture and
  skill-filter assertions use `poker-domain-rules` / `python-testing` → repoint to
  real imported names (router tests use stub JSON, so they only need VALID catalog
  names; e.g. use `systematic-debugging` / `test-driven-development`).
- `tests/test_run_traced.py` (lines 227, 235): a `code_fix` classification injects
  `poker-domain-rules` → repoint to an imported name.
- `tests/test_skills.py`: uses `_write_skill(tmp_path, ...)` to create SYNTHETIC
  skills in a temp dir — it tests the loader mechanism, not the bundled set, so it
  needs **no change** (do not touch its tmp-dir fixtures).

New test (`tests/test_system_skills.py` or added to test_skills.py):
- all 4 system skills appear in `load_catalog(skills_dirs())` with non-empty
  descriptions; each composes to a non-empty body via `compose`;
- the 3 removed skills are absent from the catalog;
- every shipped `harness/skills/*/SKILL.md` has frontmatter `name` == dir name
  (catches a bad copy).

### E. README

Document the bundled system skills: what the 4 are, that the Router auto-selects
them per request, and that user skills in `~/.config/harness/skills/` override a
system skill by the same name. (Confirm `--yolo` is in the flags table while
there; add if missing.)

## Data flow (unchanged path, better catalog)

The existing Phase 2/3 path, now fed a real catalog:

```
prompt → Router.classify(prompt) with catalog = load_catalog(skills_dirs())
       → Classification(task_type, skills=[…selected from the 4…])
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

- `tests/test_skills.py` — catalog has all 4; each composes non-empty; removed 3
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
