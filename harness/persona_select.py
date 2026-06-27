"""Persona selection: resolve a persona id to its workspace directory.

The ONE selection chokepoint. None / "default" → the built-in default workspace;
a named id → config_dir()/agents/<id> IF it exists; a missing id is a hard error
(UnknownPersona) — selection is explicit, never a silent fallback to default.
Creation of new workspaces is out of scope (Phase D)."""

from __future__ import annotations

from pathlib import Path

from harness import paths

RESERVED_KEY = "default"


class UnknownPersona(Exception):
    """Raised when --persona names a workspace that does not exist."""


def _agents_dir() -> Path:
    return paths.config_dir() / "agents"


def resolve_workspace(persona_id: str | None) -> Path:
    """Resolve persona_id to its workspace dir. None/"default" → the built-in
    default workspace; a named id → agents/<id> if the dir exists, else raise
    UnknownPersona(persona_id)."""
    if persona_id is None or persona_id == RESERVED_KEY:
        return paths.default_workspace_dir()
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
