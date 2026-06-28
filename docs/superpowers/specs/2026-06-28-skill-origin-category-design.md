# Skill `origin` + `category` axes — design

**Date:** 2026-06-28
**Status:** Spec for review (implementation deferred)
**Branch:** `skill-origin-category`

## Problem

When a user asks Done "what skills do I have?", the answer is a flat list of
`name — description` lines (`harness/skills.py:164`). There is no way to **group**
them, because the only metadata the model sees per skill is the name and the
description. The catalog discards everything else before composing the menu.

Two distinct things the user actually wants to know are both missing:

1. **Origin** — *where a skill comes from*: is it part of the harness's curated
   spine (the "secret sauce"), did the user install it globally, did this project
   add it, or can we not tell? This is the high-value question when the skill list
   grows: "which of these are just the standard global ones vs. something added
   here?"
2. **Category** — *what a skill is about*: its semantic family (e.g. `caveman`,
   `process`, `design`).

Today neither axis exists. This spec adds both.

A second, product-level requirement falls out of the origin axis: the
**bundled** skills are the harness's opinionated secret sauce. They must stay
**fully active for the model** (the routing behavior is the whole point) but be
**suppressed from user-facing surfaces** — the user never sees them enumerated,
even though Done silently uses them.

## Goals

- The model's prompt menu carries both `origin` and `category` per skill and is
  grouped by origin so the model can reason about / answer grouping requests.
- The user-facing capability answer ("what skills do I have") **omits bundled
  skills entirely** — they are used silently, never listed.
- `origin` is **derived from the load path**, not authored — zero per-skill
  authoring burden, and it cannot be spoofed by a skill's own frontmatter.
- `category` is authored in frontmatter with a safe `"other"` fallback, so the
  19 existing skills work untouched and can be backfilled over time.
- No regression to the existing no-op behavior (no skills → empty menu) or to the
  `skipped` / `shadowed` surfacing already in place.

## Non-goals

- No persona-specific skills root is being added. `persona` is reserved as a
  forward-compatible origin value but **nothing emits it today** — there is no
  persona skills root in `skills_dirs` (`harness/paths.py:50`). Wiring one is out
  of scope; the enum slot just avoids a future migration.
- No change to skill *selection*/routing, the `load_skill` tool, `flows` scoping,
  or `model_invocable` / `user_invocable`.
- No escape hatch / `--show-bundled` toggle (the user chose "use silently, never
  enumerate" with no reveal). Can be added later if needed.
- No user-facing rendering of `category` grouping in this pass — the user listing
  only gains the bundled filter. (The model menu gets the full grouping.) Grouping
  the *user* listing by category is a natural follow-up but not required to answer
  the original question.

## The two axes are orthogonal

| Axis | Source | Spoofable? | Values |
|------|--------|-----------|--------|
| `origin` | Load path (which root) | No | `bundled` `user` `project` `persona` `unknown` |
| `category` | Frontmatter `category:` | Yes (it's authored) | any string; default `"other"` |

This asymmetry is the central design decision: **`origin` is derived, `category`
is authored.** A skill file cannot lie about where it came from, but it names its
own category.

## Origin buckets ↔ roots

`skills_dirs(project_cwd=...)` (`harness/paths.py:50`) returns roots lowest-
precedence first. Each maps to exactly one bucket:

| Root | Bucket |
|------|--------|
| `bundled_skills_dir()` (`harness/skills/`) | `bundled` |
| `~/.claude/skills` | `user` |
| `config_dir()/skills` (`~/.config/harness/skills`) | `user` |
| `<cwd>/.claude/skills` | `project` |
| `<cwd>/.agents/skills` | `project` |
| anything else | `unknown` |

`unknown` is the safety net: if a root is ever added/passed that doesn't match a
known path, the skill still loads and is simply labeled `unknown` rather than
mislabeled or dropped.

## Design

### 1. Two new fields on `SkillMeta`

`harness/skills.py:30` — extend the frozen dataclass:

```python
@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    model_invocable: bool = True
    user_invocable: bool = True
    flows: tuple[str, ...] = ()
    category: str = "other"      # authored: frontmatter `category:`; "other" when absent
    origin: str = "unknown"      # derived: which root; never from frontmatter
```

Defaults preserve every existing construction site and test that builds a
`SkillMeta` positionally or with the old kwargs.

- **`category`** is parsed in `_meta_from_frontmatter` (`harness/skills.py:42`),
  mirroring how `flows` is read: `data.get("category")`, coerced to a string,
  falling back to `"other"` when missing or non-string. Pure, never raises (same
  contract as the rest of that function).
- **`origin`** is **not** set in `_meta_from_frontmatter` (that function only sees
  frontmatter, not the root). It defaults to `"unknown"` there and is filled in by
  the catalog loader (§2).

### 2. Deriving `origin` from the root

This is the only structurally non-trivial change. `origin` is known only in the
`for root in roots` loop of `load_catalog_with_skips` (`harness/skills.py:85`),
not inside the frontmatter builder.

**New pure helper in `harness/paths.py`**, next to `skills_dirs` (single source of
truth for the root→bucket mapping):

```python
def origin_for_root(root: Path, project_cwd: str | Path | None = None) -> str:
    """Classify a skills ROOT into an origin bucket by matching it against the
    same paths skills_dirs() builds. Pure; never raises. Unmatched -> 'unknown'."""
    root = Path(root)
    if root == bundled_skills_dir():
        return "bundled"
    if root in (Path.home() / ".claude" / "skills", config_dir() / "skills"):
        return "user"
    if project_cwd is not None:
        cwd = Path(project_cwd)
        if root in (cwd / ".claude" / "skills", cwd / ".agents" / "skills"):
            return "project"
    return "unknown"
```

**Thread it through the loader.** `load_catalog_with_skips` gains an optional
`project_cwd` param (so it can call `origin_for_root` with the same cwd
`skills_dirs` used) and stamps origin onto each winning meta via
`dataclasses.replace`:

```python
def load_catalog_with_skips(roots, project_cwd=None) -> CatalogLoad:
    ...
    for root in roots:
        origin = origin_for_root(root, project_cwd)
        ...
        merged[name] = replace(_meta_from_frontmatter(data, name), origin=origin)
```

Because later roots win (shadowing), the **winning** root's origin is recorded.
This is correct: a project copy that overrides a bundled skill becomes a
`project` skill — and is therefore no longer suppressed from the user (§3), which
is the right behavior (the user added it).

`load_catalog` (the thin historical wrapper, `harness/skills.py:119`) forwards
`project_cwd` too. `project_cwd` defaults to `None`, so any existing caller that
doesn't pass it still works — those skills just resolve as `bundled`/`user`/
`unknown` correctly (the two `project` roots only exist when `project_cwd` is
given, matching `skills_dirs`).

**Caller updates** — pass the cwd the call site already has:

- `harness/run_traced.py:172` → `load_catalog_with_skips(skills_roots, project_cwd=args.cwd)`
- `harness/acp_agent.py:439` → `load_catalog_with_skips(_skill_roots, project_cwd=state.cwd)`

(Both already compute `skills_dirs(project_cwd=...)` one line earlier, so the cwd
is in scope.)

### 3. Rendering

**Model menu — `compose_menu` (`harness/skills.py:157`).** Keeps ALL skills
(bundled included). Group by origin, category inline:

```
# Skills
## bundled
- **caveman** (caveman) — Ultra-compressed communication mode...
- **ship** (process) — Ship the current work end-to-end...
## project
- **verify** (process) — Verify a change by running the app...
## unknown
- **foo** (other) — ...
```

- Origin headings render in a fixed order: `bundled`, `user`, `project`,
  `persona`, `unknown` (skip any with no skills). Fixed order keeps the prompt
  stable turn-to-turn (no churn from dict ordering).
- Each line: `` - **{name}** ({category}) — {description} ``, mirroring the memory
  manifest's inline `type` (`harness/memory.py:108`).
- The preamble ("These skills are available… call `load_skill`…") is unchanged.
- Empty list → `""` (unchanged no-op).
- The output is **always grouped** — the old flat list is replaced, not kept as
  a fallback. With a single origin present, the menu is one heading + its lines;
  tests assert on the grouped shape, not the prior flat shape.

**User listing — `_format_catalog` (`harness/chat_handler.py:39`).** This is the
secret-sauce suppression point. Filter `origin == "bundled"` out of the catalog
**before** building the list and the count:

```python
visible = [m for m in catalog if m.origin != "bundled"]
```

- The `"I have N skills"` count reflects `visible` only — the framing the user
  wants ("these are *your* skills"). Bundled skills remain in `self._catalog`
  (model still uses them); only this formatter drops them.
- `skipped` and `shadowed` sections are **unchanged** — a malformed or overridden
  skill is still surfaced regardless of origin (the user should still learn why a
  skill they added won't load).
- Suppression lives at the **formatter**, not the catalog: the catalog stays
  complete (the model needs it), and only the one user-facing surface filters.
  This keeps the boundary narrow and the model's behavior untouched.

### Data flow

```
skills_dirs(project_cwd)  ──roots──►  load_catalog_with_skips(roots, project_cwd)
                                          │  for each root: origin_for_root(root, project_cwd)
                                          │  replace(meta, origin=...)
                                          ▼
                                   CatalogLoad.skills  (every meta has origin+category)
                                     │                         │
                        (model)      │                         │   (user)
                  compose_menu(metas)│                         │ _format_catalog(catalog)
                  group by origin,   │                         │ drop origin=="bundled"
                  category inline    ▼                         ▼
              full menu → system prompt            visible skills → chat answer
              (bundled INCLUDED)                   (bundled SUPPRESSED)
```

## Error handling / edge cases

- **Skill with no `category:`** → `"other"`. No error.
- **Ill-typed `category:`** (list, int) → coerced/ignored → `"other"`, same
  defensive contract as `flows` parsing.
- **Root path doesn't match any known bucket** → `"unknown"`. Skill still loads.
- **Frontmatter tries to set `origin:`** → ignored. `_meta_from_frontmatter` never
  reads `origin`; the loader overwrites it. Origin is unspoofable by design.
- **Bundled skill shadowed by a project copy** → winning origin is `project`;
  the skill becomes user-visible (correct — the user added the override).
- **No skills at all** → both `compose_menu` and `_format_catalog` keep their
  existing empty/no-op outputs.
- **`persona` origin** → reserved; no code path emits it yet. `compose_menu`'s
  fixed heading order includes it so it renders correctly if/when a persona root
  is added, but it is always empty today.

## Test surface

New/updated tests (target `tests/`):

1. **`origin_for_root`** — table test: each root from `skills_dirs` → expected
   bucket; an unmatched path → `unknown`; `project_cwd=None` makes the two project
   roots resolve to `unknown` (not `project`).
2. **`category` parsing** — frontmatter with `category:` → that value; absent →
   `"other"`; non-string → `"other"`.
3. **`load_catalog_with_skips` origin stamping** — skills from different roots get
   the right origin; a bundled skill shadowed by a project copy reports
   `origin="project"`.
4. **`compose_menu` grouping** — output contains `## bundled` and `## project`
   headings, headings appear in fixed order, each line shows the `(category)` tag,
   bundled skills ARE present (model sees everything).
5. **`_format_catalog` suppression** — bundled skills are ABSENT from the output;
   non-bundled present; the "N skills" count excludes bundled; `skipped` /
   `shadowed` sections still render.
6. **No-op preservation** — empty catalog → empty menu and the existing "no
   skills" answer; existing `SkillMeta(name, description)` construction still works
   (defaults).

## Files touched

| File | Change |
|------|--------|
| `harness/skills.py` | +2 fields on `SkillMeta`; parse `category` in `_meta_from_frontmatter`; `load_catalog_with_skips` gains `project_cwd`, stamps `origin` via `replace`; `load_catalog` forwards it; `compose_menu` groups by origin + inline category |
| `harness/paths.py` | new pure `origin_for_root(root, project_cwd)` next to `skills_dirs` |
| `harness/chat_handler.py` | `_format_catalog` filters `origin == "bundled"` before listing + count |
| `harness/run_traced.py` | pass `project_cwd=args.cwd` to `load_catalog_with_skips` |
| `harness/acp_agent.py` | pass `project_cwd=state.cwd` to `load_catalog_with_skips` |
| `tests/` | new tests per the test surface above |

## Open questions

None blocking. Possible follow-ups (explicitly out of scope here):

- Group the **user** listing by category once categories are backfilled.
- A `--show-bundled` debug escape hatch.
- Backfill `category:` frontmatter across the bundled spine (the spec ships the
  mechanism; the data can land incrementally — everything defaults to `other`).
