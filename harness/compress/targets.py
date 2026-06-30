"""Discover compressed-sibling sources and select the stale ones.

Pure file I/O — no model, never raises. Two surfaces:
  candidate_sources(cwd)        — every source we might compress (existing files).
  stale_existing_siblings(cwd)  — sources that have a sibling AND it's not fresh.

The second is what session-end auto-regen feeds to the detached worker; it never
includes a source that lacks a sibling (presence = opt-in)."""
from __future__ import annotations

from pathlib import Path

from harness import config, paths, persona_select
from harness.compress import sibling

PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"]
CWD_FILES = ["AGENTS.md", "CLAUDE.md"]


def _persona_workspaces() -> list[tuple[str, Path]]:
    """(persona_id, workspace_dir) for the default + every named persona. Never raises."""
    out: list[tuple[str, Path]] = []
    try:
        out.append(("default", paths.default_workspace_dir()))
    except Exception:
        pass
    try:
        for pid in persona_select.list_personas():
            if pid == "default":
                continue
            try:
                out.append((pid, persona_select.resolve_workspace(pid)))
            except Exception:
                continue
    except Exception:
        pass
    return out


def candidate_sources(cwd: Path | None = None) -> list[Path]:
    """Existing source files we could compress, honoring per-persona compress_aware."""
    sources: list[Path] = []
    for pid, ws in _persona_workspaces():
        try:
            if not config.compress_aware_pinned(pid):
                continue
        except Exception:
            continue
        for name in PERSONA_FILES:
            p = ws / name
            if p.is_file():
                sources.append(p)
    if cwd is not None:
        for name in CWD_FILES:
            p = Path(cwd) / name
            if p.is_file():
                sources.append(p)
    return sources


def _needs_rebuild(src: Path) -> bool:
    """True when src has a sibling that is not fresh. Never raises."""
    sib = sibling.sibling_path(src)
    if not sib.is_file():
        return False                       # no sibling → opt-out → never touched
    try:
        src_text = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False                       # can't read source → can't compress it
    try:
        sib_text = sib.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True                        # unreadable sibling → rebuild it
    return sibling.freshness(src_text, sib_text) != "fresh"


def stale_existing_siblings(cwd: Path | None = None) -> list[Path]:
    """Sources with an existing-but-not-fresh sibling. Pure file I/O, never raises."""
    return [s for s in candidate_sources(cwd) if _needs_rebuild(s)]
