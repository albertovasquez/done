"""Knowledge/skills CONTENT layer: discover skills on disk, build the catalog
from frontmatter, and compose selected skill bodies into one injectable block.

Separate from the Router (which SELECTS skill names) and from TracingAgent
(which INJECTS the block). This module only reads files and returns data.

Every per-skill read is wrapped so one bad skill can never abort a run: a
missing file, unreadable file, non-UTF-8 content, malformed YAML, or
frontmatter missing name/description is recorded as 'skipped' with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SkillLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)


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


def load_catalog(skills_dir: Path) -> list[tuple[str, str]]:
    """Scan skills_dir/<name>/SKILL.md, return [(name, description)] sorted by
    name. A dir whose SKILL.md is missing/malformed/name-mismatched/missing keys
    is skipped. Absent skills_dir -> []."""
    if not skills_dir.is_dir():
        return []
    catalog: list[tuple[str, str]] = []
    for child in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        try:
            data, _ = _parse_skill_md(skill_md)
            name, desc = data.get("name"), data.get("description")
            if not name or not desc:
                raise ValueError("frontmatter missing name/description")
            if name != child.name:
                raise ValueError("name mismatch")
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
            continue  # silently omit from catalog; can't select what can't be parsed
        catalog.append((name, desc))
    return catalog


def compose(skills_dir: Path, names: list[str]) -> SkillLoad:
    """Read each selected skill's SKILL.md and append its body to one block.
    Records failures in skipped; never raises."""
    load = SkillLoad()
    bodies: list[str] = []
    for name in names:
        skill_md = skills_dir / name / "SKILL.md"
        if not skill_md.is_file():
            load.skipped.append((name, "no SKILL.md"))
            continue
        try:
            data, body = _parse_skill_md(skill_md)
            if data.get("name") != name:
                raise ValueError("name mismatch")
        except (OSError, UnicodeDecodeError) as e:
            load.skipped.append((name, f"unreadable: {type(e).__name__}"))
            continue
        except (yaml.YAMLError, ValueError) as e:
            load.skipped.append((name, f"bad frontmatter: {e}"))
            continue
        bodies.append(f"## {name}\n{body}")
        load.injected.append(name)
    if bodies:
        load.block = ("\n\n# Available Skills\n\n"
                      "The following skills apply to this task. Follow them.\n\n"
                      + "\n\n".join(bodies))
    return load
