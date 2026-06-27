"""Per-workspace persona.toml reader — NON-model static config only.

Currently exposes extra skill roots (Phase D's D4 config surface). The worker
model is deliberately NOT here: it is single-homed in done.conf [agents.<id>]
(see config.py) to avoid a dual-writer clobber. Best-effort, like config.load:
a missing/corrupt/empty file or a missing/ill-typed key degrades to []."""

from __future__ import annotations

import tomllib
from pathlib import Path

PERSONA_TOML = "persona.toml"


def read_skills(workspace_dir: Path | None) -> list[Path]:
    """Extra skill roots declared in <workspace_dir>/persona.toml `skills`.
    Returns [] when the dir/file is absent, unreadable, corrupt, or the key is
    missing or not a list of strings. `~` is expanded; relative paths are left
    as-is (resolved by the caller against its own base)."""
    if workspace_dir is None:
        return []
    path = workspace_dir / PERSONA_TOML
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []
    skills = data.get("skills")
    if not isinstance(skills, list):
        return []
    return [Path(s).expanduser() for s in skills if isinstance(s, str)]
