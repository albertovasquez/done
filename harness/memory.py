"""Persona MEMORY content layer: read a workspace's memory files (MEMORY.md +
memory/<today>.md + memory/<yesterday>.md) into one injectable block.

Parallel to persona.py; reuses its _meaningful/_trim discipline. The block is
CONTENT-GATED: it is empty unless at least one memory file has real content, so a
seeded-but-unused default persona stays byte-identical (the Phase A no-op). When
non-empty, the block carries a protocol preamble teaching the agent how to write
to its memory via plain shell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from harness.persona import _meaningful, _trim

MEMORY_FILE = "MEMORY.md"
MEMORY_DIR = "memory"
MAX_MEMORY_CHARS = 8000


@dataclass
class MemoryLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _protocol(workspace: Path) -> str:
    """The write-protocol preamble, with absolute, double-quoted paths (the
    workspace is under the XDG/home config dir and may contain spaces)."""
    ws = str(workspace)
    mem = f'{ws}/{MEMORY_DIR}'
    return (
        "You have a persistent memory in this workspace; its files appear above "
        "(when present). To record something worth remembering:\n"
        f'1. ensure the dir exists: `mkdir -p "{mem}"`\n'
        '2. read before writing: `test -f "<file>" && cat "<file>"`\n'
        "3. append a concrete entry: `printf '%s\\n' \"...\" >> \"<file>\"`\n"
        "Write only real updates — decisions, preferences, constraints, open "
        "loops. Never write empty placeholders. Durable facts go in "
        f'`"{ws}/{MEMORY_FILE}"`; today\'s notes go in '
        f'`"{mem}/<today>.md"`. You may re-read any memory file anytime.'
    )


def _read_section(workspace: Path, rel: str, label: str,
                  load: MemoryLoad) -> str | None:
    """Read one memory file; return its '## label\\nbody' section, or None when
    missing/blank/inert/unreadable (recording skips). Never raises."""
    path = workspace / rel
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        load.skipped.append((rel, type(e).__name__))
        return None
    if not _meaningful(raw):
        load.skipped.append((rel, "blank"))
        return None
    body, trimmed = _trim(raw, MAX_MEMORY_CHARS)
    if trimmed:
        body = body + "\n\n…[truncated]…"
    load.injected.append(rel)
    return f"## {label}\n{body}"


def resolve_memory(workspace_dir: Path | None, *, today: date) -> MemoryLoad:
    """Read MEMORY.md + today's + yesterday's daily notes into one content-gated
    block. None/absent/empty/inert => empty MemoryLoad (no block, no protocol)."""
    load = MemoryLoad()
    if workspace_dir is None:
        return load
    workspace = Path(workspace_dir)
    if not workspace.is_dir():
        return load
    yesterday = today - timedelta(days=1)
    sections = []
    for rel, label in [
        (MEMORY_FILE, "MEMORY.md"),
        (f"{MEMORY_DIR}/{today.isoformat()}.md", f"memory/{today.isoformat()}"),
        (f"{MEMORY_DIR}/{yesterday.isoformat()}.md", f"memory/{yesterday.isoformat()}"),
    ]:
        section = _read_section(workspace, rel, label, load)
        if section is not None:
            sections.append(section)
    if load.injected:                       # CONTENT-GATED: only when something was read
        load.block = ("\n\n# Memory\n\n" + _protocol(workspace) + "\n\n"
                      + "\n\n".join(sections))
    return load
