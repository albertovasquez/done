"""Persona selection: resolve a persona id to its workspace directory.

The ONE selection chokepoint. None / "default" → the built-in default workspace;
a named id → config_dir()/agents/<id> IF it exists; a missing id is a hard error
(UnknownPersona) — selection is explicit, never a silent fallback to default.
Creation of new workspaces is out of scope (Phase D)."""

from __future__ import annotations

import re
from pathlib import Path

from harness import paths

RESERVED_KEY = "default"

# Allowed charset for persona ids: lowercase letters, digits, hyphen, underscore.
# Dots are excluded because they produce TOML nested-table keys that silently
# lose persisted model config; spaces and other special chars produce invalid TOML.
_VALID_ID = re.compile(r"^[a-z0-9_-]+$")


def slugify_persona_name(raw: str) -> str:
    """Normalize a free-text persona name to a safe id: lowercase, every run of
    characters outside [a-z0-9] becomes a single hyphen, leading/trailing hyphens
    trimmed. Returns "" when nothing valid survives (e.g. "!!!", emoji, ""). A
    non-empty result ALWAYS satisfies _VALID_ID — slugify is the friendly layer in
    front of the strict storage charset; accented/non-ascii chars are dropped, not
    transliterated (the typed name is kept separately as the display label)."""
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)   # any non-[a-z0-9] run -> one hyphen
    return s.strip("-")


class UnknownPersona(Exception):
    """Raised when --persona names a workspace that does not exist."""


class InvalidPersonaId(Exception):
    """Raised when --persona contains characters that are unsafe in TOML keys.

    Allowed: lowercase letters, digits, hyphen, underscore (^[a-z0-9_-]+$).
    str(e) is the offending id."""


def _agents_dir() -> Path:
    return paths.config_dir() / "agents"


def resolve_workspace(persona_id: str | None) -> Path:
    """Resolve persona_id to its workspace dir. None/"default" → the built-in
    default workspace; a named id → agents/<id> if the dir exists, else raise
    UnknownPersona(persona_id).  Raises InvalidPersonaId if the id contains
    characters that are unsafe as TOML table keys."""
    if persona_id is None or persona_id == RESERVED_KEY:
        return paths.default_workspace_dir()
    if not _VALID_ID.match(persona_id):
        raise InvalidPersonaId(persona_id)
    target = _agents_dir() / persona_id
    if not target.is_dir():
        raise UnknownPersona(persona_id)
    return target


def list_personas() -> list[str]:
    """Sorted ids of existing persona workspaces (subdirectories of agents/).
    Read-only: never creates anything. Returns [] when agents/ is absent."""
    agents = _agents_dir()
    try:
        return sorted(p.name for p in agents.iterdir() if p.is_dir())
    except OSError:
        return []
