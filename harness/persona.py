"""Persona/workspace CONTENT layer: read a workspace's identity-trio files
(SOUL.md, IDENTITY.md, USER.md) and compose them into one injectable block.

Parallel to skills.py: this module only reads files and returns data. It never
injects (consumers do) and never selects which workspace (Phase C does). Every
per-file read is wrapped so one bad/missing file can never abort a turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harness import skills

PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md"]   # order = injection order
MAX_FILE_CHARS = 8000                                   # per-file trim ceiling


@dataclass
class PersonaLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (filename, reason)


@dataclass
class TurnContext:
    """The injectable context for one turn: persona (identity) + skills (task).
    The single object every dispatch path consumes so persona reaches all of
    them without per-site re-wiring."""
    persona_block: str = ""
    skill_block: str = ""
    persona: PersonaLoad = field(default_factory=PersonaLoad)
    skills: "skills.SkillLoad" = field(default_factory=lambda: skills.SkillLoad())


def compose_context(workspace_dir: Path | None, skill_roots: list[Path],
                    skill_names: list[str]) -> TurnContext:
    """Resolve persona + skills for one turn. `workspace_dir=None` => no persona
    (persona_block stays ""). Skills always resolve from skill_roots/skill_names."""
    persona = compose_persona(workspace_dir) if workspace_dir is not None else PersonaLoad()
    skill_load = skills.compose(skill_roots, skill_names)
    return TurnContext(persona_block=persona.block, skill_block=skill_load.block,
                       persona=persona, skills=skill_load)


def _trim(text: str, limit: int) -> tuple[str, bool]:
    """Cap text at `limit` chars. Returns (text, was_trimmed)."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def compose_persona(workspace_dir: Path) -> PersonaLoad:
    """Read the identity trio from `workspace_dir` and compose one block. Absent
    dir, missing files, and blank (whitespace-only) files yield an empty/partial
    block, never a raise. Oversized files are trimmed with a marker."""
    load = PersonaLoad()
    workspace_dir = Path(workspace_dir)
    if not workspace_dir.is_dir():           # absent workspace -> empty no-op
        return load
    sections: list[str] = []
    for name in PERSONA_FILES:
        path = workspace_dir / name
        try:
            raw = path.read_text(encoding="utf-8")   # OSError if missing, UnicodeDecodeError if binary
        except FileNotFoundError:
            continue                                  # missing file is silent (like skills)
        except (OSError, UnicodeDecodeError) as e:
            load.skipped.append((name, type(e).__name__))
            continue
        if not raw.strip():                           # blank == empty after strip
            load.skipped.append((name, "blank"))
            continue
        body, trimmed = _trim(raw, MAX_FILE_CHARS)
        if trimmed:
            body = body + "\n\n…[truncated]…"
        label = name[:-3].upper() if name.endswith(".md") else name   # "SOUL.md" -> "SOUL"
        sections.append(f"## {label}\n{body}")
        load.injected.append(name)
    if sections:
        load.block = ("\n\n# Persona\n\n"
                      "You are operating as the following persona. Honor it.\n\n"
                      + "\n\n".join(sections))
    return load
