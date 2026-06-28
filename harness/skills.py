"""Knowledge/skills CONTENT layer: discover skills on disk, build the catalog
from frontmatter, and compose selected skill bodies into one injectable block.

Separate from the Router (which SELECTS skill names) and from TracingAgent
(which INJECTS the block). This module only reads files and returns data.

Every per-skill read is wrapped so one bad skill can never abort a run: a
missing file, unreadable file, non-UTF-8 content, malformed YAML, or
frontmatter missing name/description is recorded as 'skipped' with a reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from harness.paths import origin_for_root

logger = logging.getLogger("harness.skills")


@dataclass
class SkillLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)


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


def _meta_from_frontmatter(data: dict, fallback_name: str) -> SkillMeta:
    """Build a SkillMeta from a parsed frontmatter dict. Pure; never raises —
    ill-typed flags/flows degrade to defaults so one odd skill can't break the
    catalog. name/description validity is enforced by the caller (load_catalog)."""
    name = data.get("name") or fallback_name
    desc = data.get("description") or ""
    model_inv = data.get("disable-model-invocation") is not True   # only literal True disables
    user_inv = data.get("user-invocable") is not False             # only literal False hides
    raw_flow = data.get("flows", data.get("flow"))
    if isinstance(raw_flow, str):
        flows: tuple[str, ...] = (raw_flow,)
    elif isinstance(raw_flow, list):
        flows = tuple(f for f in raw_flow if isinstance(f, str))
    else:
        flows = ()
    raw_cat = data.get("category")
    category = raw_cat if isinstance(raw_cat, str) and raw_cat else "other"
    return SkillMeta(name=name, description=desc, model_invocable=model_inv,
                     user_invocable=user_inv, flows=flows, category=category)


def _parse_skill_md(path: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Raises on read/parse errors — callers
    wrap. Frontmatter is the leading ---\\n...\\n--- block."""
    text = path.read_text(encoding="utf-8")  # may raise OSError / UnicodeDecodeError
    if not text.startswith("---"):
        raise ValueError("missing frontmatter fence")
    _, fm, body = text.split("---", 2)        # may raise ValueError if < 2 fences
    data = yaml.safe_load(fm)                  # may raise yaml.YAMLError
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data, body.lstrip("\n")


@dataclass
class CatalogLoad:
    """The catalog plus what was DROPPED building it. skipped surfaces malformed
    skills to the user (not just a log) — a 'the agent knows its skills' system
    must not hide a skill with no explanation. (name = the skill DIR name, since a
    skill that failed to parse may have no valid frontmatter name.)"""
    skills: list[SkillMeta] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (dir_name, reason) — dropped
    shadowed: list[tuple[str, str]] = field(default_factory=list)  # (name, winning_root) — overridden across roots


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


def load_catalog(roots: list[Path], project_cwd=None) -> list[SkillMeta]:
    """Scan each root's <name>/SKILL.md; later roots override earlier by name.
    Invalid skill dirs are omitted (can't select what can't parse). Returns the
    flat SkillMeta list (the historical signature; use load_catalog_with_skips to
    also learn what was dropped)."""
    return load_catalog_with_skips(roots, project_cwd).skills


def compose(roots: list[Path], names: list[str]) -> SkillLoad:
    """Compose selected skills' bodies. For each name, the LAST root that has a
    valid SKILL.md for it wins. Records failures in skipped; never raises."""
    load = SkillLoad()
    bodies: list[str] = []
    for name in names:
        chosen_body = None
        for root in roots:
            skill_md = Path(root) / name / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                data, body = _parse_skill_md(skill_md)
                if data.get("name") != name:
                    raise ValueError("name mismatch")
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
                continue
            chosen_body = body           # later root overrides
        if chosen_body is None:
            load.skipped.append((name, "no valid SKILL.md in any root"))
            continue
        bodies.append(f"## {name}\n{chosen_body}")
        load.injected.append(name)
    if bodies:
        load.block = ("\n\n# Available Skills\n\n"
                      "The following skills apply to this task. Follow them.\n\n"
                      + "\n\n".join(bodies))
    return load


# Fixed render order for origin groups; keeps the menu stable turn-to-turn and
# puts the curated spine first. Origins with no skills are skipped. 'global'
# (~/.claude) precedes 'user' (<config>) — the more widely-shared root first.
_ORIGIN_ORDER = ("bundled", "global", "user", "project", "persona", "unknown")


def group_by_origin(metas: list[SkillMeta]) -> list[tuple[str, list[SkillMeta]]]:
    """Group metas by origin and return (origin, metas) pairs in _ORIGIN_ORDER,
    with any unexpected origin appended alphabetically. Empty origins are omitted.
    Single source of truth so the model menu and the user-facing answer can't
    drift on origin order."""
    by_origin: dict[str, list[SkillMeta]] = {}
    for m in metas:
        by_origin.setdefault(m.origin, []).append(m)
    ordered = [o for o in _ORIGIN_ORDER if o in by_origin]
    ordered += sorted(o for o in by_origin if o not in _ORIGIN_ORDER)
    return [(o, by_origin[o]) for o in ordered]


def compose_menu(metas: list[SkillMeta]) -> str:
    """A lightweight skill MENU (names + one-line descriptions, NO bodies) for the
    agent prompt, GROUPED BY ORIGIN with the category inline. The agent pulls a
    body with the load_skill tool only when it needs it — progressive disclosure,
    so a large skill set costs ~one line each, not a wall of bodies. Empty when
    there are no skills."""
    if not metas:
        return ""
    sections = []
    for origin, group in group_by_origin(metas):
        lines = "\n".join(
            f"- **{m.name}** ({m.category}) — {m.description}" for m in group)
        sections.append(f"## {origin}\n{lines}")
    return ("\n\n# Skills\n\n"
            "These skills are available. Their full instructions are NOT loaded "
            "yet. Before doing work a skill governs, call the `load_skill` tool "
            "with its name to read its instructions. Don't load skills you won't "
            "use.\n\n" + "\n\n".join(sections))
