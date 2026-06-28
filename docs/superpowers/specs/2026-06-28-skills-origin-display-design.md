# Skills origin display — design

**Date:** 2026-06-28
**Branch:** `skills-origin-display`
**Status:** approved, ready for plan

## Problem

When the user asks the agent to "list the skills you have **in order of origin** and if
**global / user / project**", the deterministic capability answer (`_format_catalog` in
`harness/chat_handler.py`) returns a flat alphabetical list with no origin grouping. Two
root causes:

1. `_format_catalog` is a fixed template. It uses `origin` only to *filter out* bundled
   skills (PR #133), then prints `- **name** — description` flat. It cannot reflect the
   "by origin" intent in the question.
2. There is no `global` origin. `origin_for_root` (in `harness/paths.py`) collapses both
   `~/.claude/skills` and `~/.config/harness/skills` into a single `user` bucket, so even
   if we grouped, we could only ever show two user-visible buckets (`user`, `project`),
   not the three the user asked for.

### Ground truth (verified 2026-06-28)

All 12 currently-visible skills live in `~/.claude/skills` (7 of them are symlinks into
`~/.agents/skills`). The Done user dir `~/.config/harness/skills` and both project roots
(`<cwd>/.claude/skills`, `<cwd>/.agents/skills`) are empty. So today the grouped answer
must render exactly one populated group.

## Decisions (all user-approved)

- **Three user-visible buckets:** `global`, `user`, `project`. Bundled (the curated
  harness spine) stays hidden, consistent with #133.
- **Origin mapping** (`origin_for_root`):
  - `~/.claude/skills` → `"global"` (ecosystem-wide, shared across tools; where the 12 live)
  - `~/.config/harness/skills` → `"user"` (Done's own dir)
  - `<cwd>/.claude/skills`, `<cwd>/.agents/skills` → `"project"` (unchanged)
  - `harness/skills` → `"bundled"` (unchanged)
  - everything else → `"unknown"` (unchanged)
- **Render only non-empty groups**, ordered by a fixed origin order. Empty buckets are
  omitted. When project/user skills are added later, their groups appear automatically.
- **No symlink resolution.** The 7 `~/.agents` symlinks stay classified `global` via their
  `~/.claude` load path — matching how the catalog loader actually reads them.
- **Headers (user-facing):** `### Global skills  (~/.claude)`,
  `### User skills  (~/.config/harness)`, `### Project skills  (this repo)`.

## Change set

### 1. `harness/paths.py` — `origin_for_root`
Split the single `user` branch:
```python
if root == Path.home() / ".claude" / "skills":
    return "global"
if root == config_dir() / "skills":
    return "user"
```
`replace(..., origin=origin)` in `skills.py:121` stamps whatever this returns, so the new
value flows through with no other loader change.

### 2. `harness/skills.py` — origin order + menu
- `_ORIGIN_ORDER = ("bundled", "global", "user", "project", "persona", "unknown")`.
- `compose_menu` (model-facing prompt) picks up a `## global` section for free; bundled
  stays first. No structural change to progressive disclosure.

### 3. `harness/chat_handler.py` — `_format_catalog`
- Keep: the total count line, the bundled filter, the skipped/shadowed footers.
- Add: group the visible (non-bundled) metas by origin, render in `_ORIGIN_ORDER`, skip
  empty groups, emit a friendly header + path hint per group:
  - `global` → `### Global skills  (~/.claude)`
  - `user` → `### User skills  (~/.config/harness)`
  - `project` → `### Project skills  (this repo)`
  - `unknown` → `### Other skills` (defensive; no path hint)
- A shared header/order map is the single source of truth so the menu and the catalog
  can't drift on origin order.

### 4. Tests
- `tests/test_paths_origin.py`: assert `~/.claude/skills → "global"` and
  `config_dir()/skills → "user"`; keep the no-`unknown` invariant for every `skills_dir`,
  now allowing `global` in the accepted set.
- New `_format_catalog` test: with metas across `bundled`/`global`/`project`, assert the
  rendered answer contains the `### Global skills` and `### Project skills` headers, omits
  an empty `### User skills`, and contains no bundled skill name.

## Out of scope (YAGNI)

- Resolving `~/.agents` symlinks into their own origin.
- Changing the model-facing prompt's progressive-disclosure structure.
- Letting the model re-sort the deterministic answer per free-text intent.

## Success criteria

- Asking "list skills by origin / global vs user" returns groups, not a flat list.
- The 12 current skills render under one `### Global skills (~/.claude)` group; no bundled
  skill name appears; empty user/project groups are omitted.
- `compose_menu` shows a `## global` section.
- `pytest tests/ -q` green.
