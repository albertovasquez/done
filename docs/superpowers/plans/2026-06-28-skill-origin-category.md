# Skill origin + category axes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every skill an `origin` (derived from its load path) and a `category` (authored frontmatter), group the model's menu by origin, and suppress bundled skills from the user-facing capability answer.

**Architecture:** Two new fields on the frozen `SkillMeta`. `category` is parsed from frontmatter (`"other"` fallback); `origin` is stamped by the catalog loader from a new pure `origin_for_root()` helper that classifies a root path into `bundled|user|project|persona|unknown`. The model menu (`compose_menu`) renders ALL skills grouped by origin with category inline; the user listing (`_format_catalog`) filters `origin=="bundled"` out. Two call sites thread `project_cwd` into the loader so origin classification matches the roots that were scanned.

**Tech Stack:** Python 3.11+, dataclasses (`frozen=True`, `replace`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-28-skill-origin-category-design.md`

## Global Constraints

- Python floor is **3.11+** (matches the project's `requires-python`); use stdlib only, no new deps.
- `SkillMeta` is **`frozen=True`** — mutate only via `dataclasses.replace`, never attribute assignment.
- New fields MUST be defaulted so every existing `SkillMeta(...)` construction and equality assertion still holds (`tests/test_flows.py`, `tests/test_router.py`, `tests/test_skills.py`, `tests/test_chat_handler.py`).
- `origin` is **never** read from frontmatter (unspoofable). `_meta_from_frontmatter` must not look at any `origin` key.
- Origin values: exactly `bundled`, `user`, `project`, `persona`, `unknown`. `persona` is reserved — no code emits it yet.
- Preserve existing no-op behavior: empty catalog → `compose_menu` returns `""`, `_format_catalog` returns the "no skills" line.
- Preserve `skipped` / `shadowed` surfacing in `_format_catalog` regardless of origin.
- Test command from the worktree root: `.venv/bin/python -m pytest tests/ -q`. Single test: `.venv/bin/python -m pytest tests/test_skills.py::test_name -q`.

---

### Task 1: `origin_for_root` helper in paths.py

**Files:**
- Modify: `harness/paths.py` (add function after `skills_dirs`, ~line 71)
- Test: `tests/test_paths_origin.py` (create)

**Interfaces:**
- Consumes: `bundled_skills_dir()`, `config_dir()` (existing in `harness/paths.py`).
- Produces: `origin_for_root(root: Path, project_cwd: str | Path | None = None) -> str` returning one of `"bundled" | "user" | "project" | "unknown"`. (`"persona"` is in the spec's value set but this function never returns it — no persona root exists.)

- [ ] **Step 1: Write the failing test**

Create `tests/test_paths_origin.py`:

```python
from pathlib import Path

from harness.paths import (
    origin_for_root, bundled_skills_dir, config_dir, skills_dirs,
)


def test_bundled_root_is_bundled():
    assert origin_for_root(bundled_skills_dir()) == "bundled"


def test_user_roots_are_user():
    assert origin_for_root(Path.home() / ".claude" / "skills") == "user"
    assert origin_for_root(config_dir() / "skills") == "user"


def test_project_roots_are_project_only_with_cwd():
    cwd = Path("/some/proj")
    assert origin_for_root(cwd / ".claude" / "skills", project_cwd=cwd) == "project"
    assert origin_for_root(cwd / ".agents" / "skills", project_cwd=cwd) == "project"
    # without project_cwd, the same paths are NOT classified as project
    assert origin_for_root(cwd / ".claude" / "skills") == "unknown"


def test_unmatched_root_is_unknown():
    assert origin_for_root(Path("/totally/unrelated/dir")) == "unknown"


def test_every_skills_dir_classifies_without_unknown():
    cwd = Path("/proj")
    for root in skills_dirs(project_cwd=cwd):
        assert origin_for_root(root, project_cwd=cwd) in {"bundled", "user", "project"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_paths_origin.py -q`
Expected: FAIL with `ImportError: cannot import name 'origin_for_root'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/paths.py`, add immediately after `skills_dirs` returns (after line 70-71):

```python
def origin_for_root(root: Path | str, project_cwd: str | Path | None = None) -> str:
    """Classify a skills ROOT into an origin bucket by matching it against the
    same paths skills_dirs() builds. Pure; never raises. Unmatched -> 'unknown'.

    origin is DERIVED from the load path, never from a skill's frontmatter, so a
    skill cannot misrepresent where it came from. 'persona' is reserved in the
    value set but never returned (no persona skills root exists yet)."""
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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_paths_origin.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/paths.py tests/test_paths_origin.py
git commit -m "feat(skills): add origin_for_root path classifier"
```

---

### Task 2: `category` + `origin` fields on SkillMeta; parse `category`

**Files:**
- Modify: `harness/skills.py:30-39` (dataclass), `harness/skills.py:42-58` (`_meta_from_frontmatter`)
- Test: `tests/test_skills.py` (add tests near the existing `_meta_from_frontmatter` tests, ~line 122)

**Interfaces:**
- Consumes: nothing new.
- Produces: `SkillMeta` gains `category: str = "other"` and `origin: str = "unknown"`. `_meta_from_frontmatter` sets `category` from frontmatter; it does NOT set `origin` (stays default `"unknown"`, stamped later by the loader in Task 3).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skills.py` (after `test_meta_flow_scalar_and_list_and_garbage`, ~line 122):

```python
def test_meta_category_present_absent_and_garbage():
    # present -> that value
    assert _meta_from_frontmatter(
        {"name": "x", "description": "d", "category": "caveman"}, "x").category == "caveman"
    # absent -> "other"
    assert _meta_from_frontmatter({"name": "x", "description": "d"}, "x").category == "other"
    # non-string -> "other" (never raises)
    assert _meta_from_frontmatter(
        {"name": "x", "description": "d", "category": ["a", "b"]}, "x").category == "other"
    assert _meta_from_frontmatter(
        {"name": "x", "description": "d", "category": 7}, "x").category == "other"


def test_meta_origin_defaults_unknown_and_ignores_frontmatter():
    # _meta_from_frontmatter never reads origin: it stays the default "unknown"
    # even if a skill tries to set it (origin is derived, not authored).
    m = _meta_from_frontmatter(
        {"name": "x", "description": "d", "origin": "bundled"}, "x")
    assert m.origin == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skills.py::test_meta_category_present_absent_and_garbage tests/test_skills.py::test_meta_origin_defaults_unknown_and_ignores_frontmatter -q`
Expected: FAIL — `AttributeError: 'SkillMeta' object has no attribute 'category'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/skills.py`, extend the dataclass (lines 30-39) — add two fields after `flows`:

```python
@dataclass(frozen=True)
class SkillMeta:
    """A skill's catalog record: identity plus its invocation model. Replaces the
    old flat (name, description) tuple so the router can honor disable-model-
    invocation and flow scoping. Defaults reproduce the pre-metadata behavior."""
    name: str
    description: str
    model_invocable: bool = True      # False == disable-model-invocation (user/explicit only)
    user_invocable: bool = True       # False == not exposed as /name
    flows: tuple[str, ...] = ()       # () == global (always available); else flow families
    category: str = "other"           # authored: frontmatter `category:`; "other" when absent
    origin: str = "unknown"           # DERIVED from the load root (Task 3); never from frontmatter
```

In `_meta_from_frontmatter` (lines 42-58), parse `category` and pass it. Add before the `return`:

```python
    raw_cat = data.get("category")
    category = raw_cat if isinstance(raw_cat, str) and raw_cat else "other"
    return SkillMeta(name=name, description=desc, model_invocable=model_inv,
                     user_invocable=user_inv, flows=flows, category=category)
```

(Leave `origin` unset — it defaults to `"unknown"` here and is stamped by the loader in Task 3.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skills.py -q`
Expected: PASS — the two new tests pass AND the existing equality tests (`test_meta_defaults_when_only_name_desc` at :97, `test_load_catalog_returns_skillmeta` at :125) still pass because the new defaults apply to both sides of `==`.

- [ ] **Step 5: Commit**

```bash
git add harness/skills.py tests/test_skills.py
git commit -m "feat(skills): SkillMeta gains category (authored) + origin (derived) fields"
```

---

### Task 3: Stamp `origin` in `load_catalog_with_skips`; thread `project_cwd`

**Files:**
- Modify: `harness/skills.py:85-124` (`load_catalog_with_skips` + `load_catalog` wrapper), and the imports at the top of `harness/skills.py`
- Test: `tests/test_skills.py` (add after the shadow tests, ~line 200)

**Interfaces:**
- Consumes: `origin_for_root` from Task 1, `SkillMeta.origin` from Task 2, `dataclasses.replace`.
- Produces: `load_catalog_with_skips(roots, project_cwd=None) -> CatalogLoad` where every `meta.origin` reflects the WINNING root. `load_catalog(roots, project_cwd=None) -> list[SkillMeta]` forwards `project_cwd`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skills.py` (after `test_shadowed_records_later_root_win`, ~line 200):

```python
def test_origin_stamped_from_winning_root(tmp_path, monkeypatch):
    # Point the bundled root at a temp dir we control, then verify a skill loaded
    # from it gets origin="bundled" and one from a project root gets "project".
    import harness.paths as paths
    bundled = tmp_path / "bundled"; (bundled / "a").mkdir(parents=True)
    (bundled / "a" / "SKILL.md").write_text(
        "---\nname: a\ndescription: bundled A\n---\nbody\n")
    proj = tmp_path / "proj" / ".agents" / "skills"; (proj / "b").mkdir(parents=True)
    (proj / "b" / "SKILL.md").write_text(
        "---\nname: b\ndescription: project B\n---\nbody\n")
    monkeypatch.setattr(paths, "bundled_skills_dir", lambda: bundled)

    from harness.skills import load_catalog_with_skips
    cwd = tmp_path / "proj"
    load = load_catalog_with_skips([bundled, proj], project_cwd=cwd)
    by = {m.name: m.origin for m in load.skills}
    assert by == {"a": "bundled", "b": "project"}


def test_origin_uses_winning_root_when_shadowed(tmp_path, monkeypatch):
    # A bundled skill overridden by a project copy reports origin="project".
    import harness.paths as paths
    bundled = tmp_path / "bundled"; (bundled / "a").mkdir(parents=True)
    (bundled / "a" / "SKILL.md").write_text(
        "---\nname: a\ndescription: bundled A\n---\nb\n")
    proj = tmp_path / "proj" / ".agents" / "skills"; (proj / "a").mkdir(parents=True)
    (proj / "a" / "SKILL.md").write_text(
        "---\nname: a\ndescription: project A wins\n---\nb\n")
    monkeypatch.setattr(paths, "bundled_skills_dir", lambda: bundled)

    from harness.skills import load_catalog_with_skips
    cwd = tmp_path / "proj"
    load = load_catalog_with_skips([bundled, proj], project_cwd=cwd)
    [m] = load.skills
    assert m.origin == "project" and m.description == "project A wins"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skills.py::test_origin_stamped_from_winning_root tests/test_skills.py::test_origin_uses_winning_root_when_shadowed -q`
Expected: FAIL — `TypeError: load_catalog_with_skips() got an unexpected keyword argument 'project_cwd'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/skills.py`, add to the imports at the top (after `from pathlib import Path`, line 16):

```python
from dataclasses import dataclass, field, replace
```

(The module already imports `dataclass, field`; add `replace` to that line.)

Add the origin import — at the top of the module with the others:

```python
from harness.paths import origin_for_root
```

> NOTE for implementer: confirm this import does not create a cycle. `harness/paths.py` imports only stdlib + `dotenv` (verified: no `import harness.skills`), so `skills.py` importing `paths.py` is safe. If a future cycle appears, import `origin_for_root` lazily inside the function instead.

Change `load_catalog_with_skips` signature and body (lines 85-116):

```python
def load_catalog_with_skips(roots: list[Path], project_cwd=None) -> CatalogLoad:
    """Scan each root's <name>/SKILL.md; later roots override earlier by name.
    Returns the valid SkillMeta list (each stamped with the origin of its WINNING
    root), every dir DROPPED (with a human reason), and every skill SHADOWED across
    roots (a later root won) so a name clash is visible rather than silent. Never
    raises. project_cwd lets origin_for_root classify the two project roots; pass
    the same cwd skills_dirs() was built with."""
    merged: dict[str, SkillMeta] = {}
    skipped: list[tuple[str, str]] = []
    shadowed: list[tuple[str, str]] = []
    for root in roots:
        origin = origin_for_root(root, project_cwd)
        if not Path(root).is_dir():
            continue
        for child in sorted(Path(root).iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            try:
                data, _ = _parse_skill_md(child / "SKILL.md")
                name, desc = data.get("name"), data.get("description")
                if not name or not desc:
                    raise ValueError("frontmatter missing name/description")
                if name != child.name:
                    raise ValueError(f"name '{name}' does not match directory '{child.name}'")
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as e:
                logger.warning("skipping skill %s/SKILL.md: %s", child.name, e)
                skipped.append((child.name, str(e)))
                continue
            if name in merged:                       # an earlier root had this name
                shadowed.append((name, str(root)))
            merged[name] = replace(_meta_from_frontmatter(data, name), origin=origin)
    return CatalogLoad(skills=[merged[k] for k in sorted(merged)],
                       skipped=skipped, shadowed=shadowed)
```

Update the `load_catalog` wrapper (lines 119-124) to forward `project_cwd`:

```python
def load_catalog(roots: list[Path], project_cwd=None) -> list[SkillMeta]:
    """Scan each root's <name>/SKILL.md; later roots override earlier by name.
    Invalid skill dirs are omitted (can't select what can't parse). Returns the
    flat SkillMeta list (the historical signature; use load_catalog_with_skips to
    also learn what was dropped)."""
    return load_catalog_with_skips(roots, project_cwd).skills
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skills.py -q`
Expected: PASS — new origin tests pass; existing catalog tests still pass (skills loaded from `tmp_path` roots that match no known root get `origin="unknown"`, which their assertions don't check).

- [ ] **Step 5: Commit**

```bash
git add harness/skills.py tests/test_skills.py
git commit -m "feat(skills): stamp origin from winning root in load_catalog_with_skips"
```

---

### Task 4: Group `compose_menu` by origin, category inline

**Files:**
- Modify: `harness/skills.py:157-169` (`compose_menu`)
- Test: `tests/test_skills.py` — UPDATE `test_compose_menu_lists_names_not_bodies` (line 136) and add a grouping test

**Interfaces:**
- Consumes: `SkillMeta.origin`, `SkillMeta.category` from Tasks 2-3.
- Produces: `compose_menu(metas)` returns a string with `## <origin>` headings in fixed order `bundled, user, project, persona, unknown` (empty origins skipped), each skill line `- **{name}** ({category}) — {description}`. Empty list → `""` (unchanged).

- [ ] **Step 1: Write the failing test**

In `tests/test_skills.py`, REPLACE `test_compose_menu_lists_names_not_bodies` (lines 136-141) with:

```python
def test_compose_menu_groups_by_origin_with_category():
    from harness.skills import compose_menu
    metas = [
        SkillMeta("a", "does A", category="caveman", origin="bundled"),
        SkillMeta("v", "does V", category="process", origin="project"),
        SkillMeta("u", "does U", origin="unknown"),  # category defaults to "other"
    ]
    out = compose_menu(metas)
    # preamble + load_skill instruction preserved
    assert "# Skills" in out and "load_skill" in out
    # origin headings present
    assert "## bundled" in out and "## project" in out and "## unknown" in out
    # bundled appears before project before unknown (fixed order)
    assert out.index("## bundled") < out.index("## project") < out.index("## unknown")
    # each line carries name, category tag, and description (no bodies)
    assert "- **a** (caveman) — does A" in out
    assert "- **v** (process) — does V" in out
    assert "- **u** (other) — does U" in out
```

(Keep `test_compose_menu_empty_is_blank` at line 144 unchanged.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skills.py::test_compose_menu_groups_by_origin_with_category -q`
Expected: FAIL — current `compose_menu` emits a flat list with no `## bundled` heading and no `(category)` tag.

- [ ] **Step 3: Write minimal implementation**

Replace `compose_menu` (lines 157-169) in `harness/skills.py`:

```python
# Fixed render order for origin groups; keeps the menu stable turn-to-turn and
# puts the curated spine first. Origins with no skills are skipped.
_ORIGIN_ORDER = ("bundled", "user", "project", "persona", "unknown")


def compose_menu(metas: list[SkillMeta]) -> str:
    """A lightweight skill MENU (names + one-line descriptions, NO bodies) for the
    agent prompt, GROUPED BY ORIGIN with the category inline. The agent pulls a
    body with the load_skill tool only when it needs it — progressive disclosure,
    so a large skill set costs ~one line each, not a wall of bodies. Empty when
    there are no skills."""
    if not metas:
        return ""
    by_origin: dict[str, list[SkillMeta]] = {}
    for m in metas:
        by_origin.setdefault(m.origin, []).append(m)
    # known origins in fixed order, then any unexpected origin value, alphabetical
    ordered = [o for o in _ORIGIN_ORDER if o in by_origin]
    ordered += sorted(o for o in by_origin if o not in _ORIGIN_ORDER)
    sections = []
    for origin in ordered:
        lines = "\n".join(
            f"- **{m.name}** ({m.category}) — {m.description}" for m in by_origin[origin])
        sections.append(f"## {origin}\n{lines}")
    return ("\n\n# Skills\n\n"
            "These skills are available. Their full instructions are NOT loaded "
            "yet. Before doing work a skill governs, call the `load_skill` tool "
            "with its name to read its instructions. Don't load skills you won't "
            "use.\n\n" + "\n\n".join(sections))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skills.py -q`
Expected: PASS — new grouping test passes, `test_compose_menu_empty_is_blank` still passes.

- [ ] **Step 5: Commit**

```bash
git add harness/skills.py tests/test_skills.py
git commit -m "feat(skills): group skill menu by origin with category inline"
```

---

### Task 5: Suppress bundled skills from the user capability answer

**Files:**
- Modify: `harness/chat_handler.py:39-65` (`_format_catalog`)
- Test: `tests/test_skills.py` (add near the existing `_format_catalog` tests, ~line 188) — or `tests/test_chat_handler.py`; use `tests/test_skills.py` to keep `_format_catalog` tests together.

**Interfaces:**
- Consumes: `SkillMeta.origin` from Tasks 2-3.
- Produces: `_format_catalog(catalog, skipped=None, shadowed=None)` — same signature — but skills with `origin == "bundled"` are excluded from the listed lines AND the "N skills" count. `skipped`/`shadowed` sections unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skills.py` (after `test_format_catalog_no_skips_unchanged`, ~line 188):

```python
def test_format_catalog_suppresses_bundled():
    from harness.chat_handler import _format_catalog
    cat = [
        SkillMeta("caveman", "secret sauce", origin="bundled"),
        SkillMeta("my-skill", "user added", origin="user"),
        SkillMeta("proj-skill", "project added", origin="project"),
    ]
    out = _format_catalog(cat)
    # bundled skill is NOT listed
    assert "caveman" not in out and "secret sauce" not in out
    # user + project skills ARE listed
    assert "my-skill" in out and "proj-skill" in out
    # count reflects only the 2 visible skills, not 3
    assert "**2 skills**" in out


def test_format_catalog_all_bundled_reads_as_no_skills():
    from harness.chat_handler import _format_catalog
    out = _format_catalog([SkillMeta("caveman", "x", origin="bundled")])
    # nothing visible -> the honest "no skills" framing
    assert "no skills" in out.lower()


def test_format_catalog_bundled_filtered_but_skipped_kept():
    from harness.chat_handler import _format_catalog
    out = _format_catalog(
        [SkillMeta("caveman", "x", origin="bundled"),
         SkillMeta("mine", "y", origin="user")],
        skipped=[("broken", "frontmatter is not a mapping")])
    assert "caveman" not in out          # bundled still suppressed
    assert "mine" in out                 # user skill shown
    assert "broken" in out               # skipped section unaffected by origin
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest "tests/test_skills.py::test_format_catalog_suppresses_bundled" "tests/test_skills.py::test_format_catalog_all_bundled_reads_as_no_skills" "tests/test_skills.py::test_format_catalog_bundled_filtered_but_skipped_kept" -q`
Expected: FAIL — bundled skills currently appear and the count is 3, not 2.

- [ ] **Step 3: Write minimal implementation**

In `harness/chat_handler.py`, edit `_format_catalog` (lines 39-65). Replace the body's catalog handling — add the filter at the top and use `visible` for the count and lines:

```python
def _format_catalog(catalog: "list[skills.SkillMeta]",
                    skipped: "list[tuple[str, str]] | None" = None,
                    shadowed: "list[tuple[str, str]] | None" = None) -> str:
    """A markdown answer listing the user's skills (name + description). Skills
    whose origin is 'bundled' (the harness's curated spine) are NOT enumerated —
    they are used silently. Dropped skills (skipped) and overridden skills
    (shadowed) are listed regardless of origin, so the user still learns why a
    skill they added is unselectable or which copy is active."""
    skipped = skipped or []
    shadowed = shadowed or []
    visible = [m for m in catalog if getattr(m, "origin", "unknown") != "bundled"]
    if not visible:
        head = ("I currently have **no skills** loaded — none are bundled or "
                "configured in your skills directories.")
        lines = [head]
    else:
        n = len(visible)
        lines = [f"I have **{n} skill{'s' if n != 1 else ''}** available:", ""]
        lines += [f"- **{m.name}** — {m.description}" for m in visible]
    if skipped:
        k = len(skipped)
        lines += ["", f"⚠️ **{k} skill{'s' if k != 1 else ''} skipped** (won't load):"]
        lines += [f"- `{name}` — {reason}" for name, reason in skipped]
    if shadowed:
        s = len(shadowed)
        lines += ["", f"ℹ️ **{s} skill{'s' if s != 1 else ''} overridden** (a higher-precedence root won):"]
        lines += [f"- `{name}` — using the copy in `{root}`" for name, root in shadowed]
    return "\n".join(lines)
```

(`getattr(..., "origin", "unknown")` defends against any caller still passing a bare `(name, description)` tuple-shaped object; real `SkillMeta`s always have `origin`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skills.py -q`
Expected: PASS — new suppression tests pass. NOTE: `test_format_catalog_surfaces_skipped` (:174) and `test_format_catalog_no_skips_unchanged` (:184) build `SkillMeta("good", "fine")` whose `origin` defaults to `"unknown"` (not bundled), so they remain visible and those tests still pass.

- [ ] **Step 5: Commit**

```bash
git add harness/chat_handler.py tests/test_skills.py
git commit -m "feat(chat): suppress bundled skills from the capability answer"
```

---

### Task 6: Thread `project_cwd` into the two real call sites

**Files:**
- Modify: `harness/run_traced.py:172`
- Modify: `harness/acp_agent.py:439`
- Test: covered by the existing suite (no new unit test — these are wiring changes verified by the full run). Add one assertion-light integration check only if a call site already has a test harness; otherwise rely on the suite.

**Interfaces:**
- Consumes: `load_catalog_with_skips(roots, project_cwd=...)` from Task 3.
- Produces: both call sites pass the same cwd they built `skills_dirs` with, so origin is classified correctly at runtime (otherwise project skills would resolve as `unknown`).

- [ ] **Step 1: Make the edits**

In `harness/run_traced.py`, line 172, change:

```python
    _catalog_load = skills.load_catalog_with_skips(skills_roots)
```

to:

```python
    _catalog_load = skills.load_catalog_with_skips(skills_roots, project_cwd=args.cwd)
```

In `harness/acp_agent.py`, line 439, change:

```python
        _catalog_load = skills.load_catalog_with_skips(_skill_roots)
```

to:

```python
        _catalog_load = skills.load_catalog_with_skips(_skill_roots, project_cwd=state.cwd)
```

- [ ] **Step 2: Verify nothing else passes roots without cwd**

Run: `grep -rn "load_catalog_with_skips\|load_catalog(" harness/`
Expected: every call in `harness/` either passes `project_cwd=` or is a place where project skills can't exist (none remain after the two edits above). Confirm no other production call site builds `skills_dirs(project_cwd=...)` and then drops the cwd into the loader.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all tests green (the full count should be the prior total plus the new tests added in Tasks 1-5).

- [ ] **Step 4: Commit**

```bash
git add harness/run_traced.py harness/acp_agent.py
git commit -m "feat(skills): thread project_cwd into catalog load for correct origin"
```

---

### Task 7: Full-suite green + self-check

**Files:** none (verification task).

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all green. If any pre-existing test that constructs `SkillMeta` or asserts on menu/catalog text fails, fix the test to expect the new grouped/filtered output (do NOT weaken the feature) and note it.

- [ ] **Step 2: Grep for any missed flat-list assumptions**

Run: `grep -rn "compose_menu\|_format_catalog" tests/`
Expected: every test referencing these expects the new shapes (grouped menu / bundled-filtered listing). Reconcile any stragglers.

- [ ] **Step 3: Sanity-check the live menu shape (optional but recommended)**

Run a quick REPL to eyeball the real bundled spine grouped:

```bash
.venv/bin/python -c "
from harness import paths, skills
roots = paths.skills_dirs(project_cwd='.')
cat = skills.load_catalog_with_skips(roots, project_cwd='.')
print(skills.compose_menu(cat.skills)[:800])
"
```

Expected: a `# Skills` block with `## bundled` (and possibly `## project`/`## user`) headings, lines like `- **caveman** (other) — ...`. Confirms origin classification works against the real filesystem.

- [ ] **Step 4: Commit any test reconciliations**

```bash
git add -A
git commit -m "test(skills): reconcile suite with grouped menu + bundled suppression"
```

(Skip if Step 1 was already green and nothing changed.)

---

## Self-Review

**Spec coverage** — every spec section maps to a task:
- SkillMeta `category` + `origin` fields → Task 2.
- `origin` derived from path, unspoofable → Task 1 (`origin_for_root`) + Task 2 (frontmatter ignores `origin`) + Task 3 (loader stamps it).
- Winning-root origin on shadowing → Task 3 (`test_origin_uses_winning_root_when_shadowed`).
- Model menu grouped by origin, category inline, fixed heading order, no-op empty → Task 4.
- User listing suppresses bundled, count excludes bundled, `skipped`/`shadowed` kept → Task 5.
- `project_cwd` threaded so runtime origin is correct → Task 6.
- `persona` reserved/unemitted → Task 1 (helper never returns it) + Task 4 (order includes it, always empty).
- No-op preservation + existing-construction safety → defaults in Task 2, verified across Tasks 4-7.

**Placeholder scan** — no TBD/TODO; every code step shows complete code; every test step shows the assertions and the exact run command + expected result.

**Type consistency** — `origin_for_root(root, project_cwd=None) -> str` is defined in Task 1 and consumed with that exact signature in Task 3 and Task 6. `SkillMeta` field names `category`/`origin` are introduced in Task 2 and used verbatim in Tasks 3, 4, 5. `load_catalog_with_skips(roots, project_cwd=None)` defined in Task 3, called with `project_cwd=` in Task 6. `_ORIGIN_ORDER` defined and used only within Task 4. No naming drift.
