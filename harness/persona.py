"""Persona/workspace CONTENT layer: read a workspace's identity-trio files
(SOUL.md, IDENTITY.md, USER.md) and compose them into one injectable block.

Parallel to skills.py: this module only reads files and returns data. It never
injects (consumers do) and never selects which workspace (Phase C does). Every
per-file read is wrapped so one bad/missing file can never abort a turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from pathlib import Path

from harness import paths
from harness import skills

logger = logging.getLogger("harness.persona")

PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md"]   # order = injection order
MAX_FILE_CHARS = 8000                                   # per-file trim ceiling

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _meaningful(raw: str) -> bool:
    """True if the file has injectable content — anything but whitespace remains
    after HTML comments are removed. A comment-only template => False (skipped,
    never injected), so shipped templates preserve the byte-identical no-op.
    HTML comments only: '#' is a Markdown heading and must NOT be treated as a
    comment."""
    return bool(_HTML_COMMENT.sub("", raw).strip())


@dataclass
class PersonaLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (filename, reason)


@dataclass
class TurnContext:
    """The injectable context for one turn: persona (identity, resolved once per
    session) + skills (task, resolved per turn). The single bundle every agent
    dispatch path consumes so persona reaches all of them without per-site
    re-wiring."""
    persona_block: str = ""
    memory_block: str = ""
    skill_block: str = ""
    skills: "skills.SkillLoad" = field(default_factory=lambda: skills.SkillLoad())


def resolve_persona(workspace_dir: Path | None) -> PersonaLoad:
    """The single persona-resolution entry point. None or absent workspace =>
    empty PersonaLoad (no persona). Callers cache `.block` per their own lifecycle
    (acp_agent caches per session on SessionState; run_traced reads once per run)."""
    if workspace_dir is None:
        return PersonaLoad()
    return compose_persona(workspace_dir)


def compose_context(persona_block: str, memory_block: str, skill_roots: list[Path],
                    skill_names: list[str]) -> TurnContext:
    """Bundle already-resolved persona + memory blocks with a fresh skill compose.
    Persona+memory resolve once per session (caller-cached); skills per turn."""
    skill_load = skills.compose(skill_roots, skill_names)
    return TurnContext(persona_block=persona_block, memory_block=memory_block,
                       skill_block=skill_load.block, skills=skill_load)


def _trim(text: str, limit: int) -> tuple[str, bool]:
    """Cap text at `limit` chars. Returns (text, was_trimmed)."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def seed_default_workspace() -> None:
    """Copy the bundled inert templates into ~/.config/harness/agents/default/ on
    first run. No-op if the dir already exists (never clobber a real workspace).
    Never overwrites a file. Best-effort: never raises into the startup path."""
    dest = paths.default_workspace_dir()
    if dest.exists():
        return                                  # user has a workspace; do not clobber
    try:
        src = paths.bundled_persona_templates_dir()
        dest.mkdir(parents=True, exist_ok=True)
        for name in PERSONA_FILES:
            s, d = src / name, dest / name
            if s.is_file() and not d.exists():
                d.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as e:
        # Read-only home etc. — never break startup, but a silent failure here
        # means the default persona templates never appear ("why is my persona
        # blank and /persona shows nothing to edit?").
        logger.warning("could not seed default persona workspace at %s (%s)", dest, e)


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
        if not _meaningful(raw):                      # blank, whitespace, or comment-only
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
