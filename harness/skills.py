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
from dataclasses import dataclass, field
from pathlib import Path

import yaml

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
    return SkillMeta(name=name, description=desc, model_invocable=model_inv,
                     user_invocable=user_inv, flows=flows)


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


def load_catalog(roots: list[Path]) -> list[SkillMeta]:
    """Scan each root's <name>/SKILL.md; later roots override earlier by name.
    Invalid skill dirs are silently omitted (can't select what can't parse).
    Returns structured SkillMeta (name, description, invocation model, flows)."""
    merged: dict[str, SkillMeta] = {}
    for root in roots:
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
                    raise ValueError("name mismatch")
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as e:
                # A malformed skill silently vanishes from the catalog — the
                # router can never select it and the user never learns why. Unlike
                # compose(), load_catalog returns a flat list with no skipped slot,
                # so a log is the only place this surfaces.
                logger.warning("skipping skill %s/SKILL.md: %s", child.name, e)
                continue
            merged[name] = _meta_from_frontmatter(data, name)   # later root wins
    return [merged[k] for k in sorted(merged)]


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
