"""Persona MEMORY content layer: read a workspace's memory files (MEMORY.md +
memory/<today>.md + memory/<yesterday>.md) into one injectable block.

Parallel to persona.py; reuses its _meaningful/_trim discipline. The block is
CONTENT-GATED: it is empty unless at least one memory file has real content, so a
seeded-but-unused default persona stays byte-identical (the Phase A no-op). When
non-empty, the block carries a protocol preamble teaching the agent how to write
to its memory via plain shell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from harness.textgate import _meaningful, _trim

MEMORY_FILE = "MEMORY.md"
MEMORY_DIR = "memory"
MAX_MEMORY_CHARS = 8000

# Daily notes are YYYY-MM-DD[.-slug].md; typed facts are arbitrary slugs. The
# manifest lists facts only, so daily notes are excluded by this shape.
_DAILY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass
class MemoryLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class MemoryMeta:
    """A typed memory fact's manifest record. Mirrors skills.SkillMeta: identity
    (name/description) plus a category. `type` is one of user/feedback/project/
    reference; an unknown value is kept verbatim (forward-compatible, never fatal)
    and `reference` is the default when absent (the least-privileged category)."""
    name: str
    description: str
    type: str = "reference"


def _meta_from_frontmatter(data: dict, fallback_name: str) -> MemoryMeta:
    """Build a MemoryMeta from a parsed frontmatter dict. Pure; never raises.
    name/description/type are COERCED to str — YAML happily parses `name: 123` as
    an int, and a non-str name would later crash `", ".join(m.name ...)`. A
    non-string/empty name falls back to the filename stem; type defaults to
    'reference' (the least-privileged category)."""
    raw_name = data.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else fallback_name
    raw_desc = data.get("description")
    desc = raw_desc if isinstance(raw_desc, str) else (
        "" if raw_desc is None else str(raw_desc))
    raw_type = data.get("type")
    mtype = raw_type if isinstance(raw_type, str) and raw_type else "reference"
    return MemoryMeta(name=name, description=desc, type=mtype)


def _frontmatter(text: str) -> dict:
    """Parse the leading ---\\n...\\n--- block into a dict. Returns {} when there
    is no frontmatter or it isn't a mapping. May raise yaml.YAMLError (caller wraps)."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else {}


def load_manifest(workspace_dir: Path | None) -> list[MemoryMeta]:
    """Parse typed per-fact files under <workspace>/memory/*.md into MemoryMeta
    records, sorted by name. Daily notes (YYYY-MM-DD*.md) are excluded — they are
    bootstrap-injected, not manifest facts. Blank/comment-only/unreadable files
    are skipped silently; this never raises (one bad fact can't break recall)."""
    if workspace_dir is None:
        return []
    mem_dir = Path(workspace_dir) / MEMORY_DIR
    if not mem_dir.is_dir():
        return []
    metas: list[MemoryMeta] = []
    for path in sorted(mem_dir.glob("*.md")):
        if _DAILY_RE.match(path.stem):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _meaningful(text):
            continue
        try:
            data = _frontmatter(text)
        except yaml.YAMLError:
            data = {}
        metas.append(_meta_from_frontmatter(data, path.stem))
    return metas


def compose_menu(metas: list[MemoryMeta]) -> str:
    """Render typed facts as a one-line-each menu the agent sees at startup (so it
    knows what to pull with load_memory). Mirrors skills.compose_menu. Empty list
    => "" (content-gated). The bodies are NOT included — only name/desc/type."""
    if not metas:
        return ""
    lines = [f"- `{m.name}` ({m.type}) — {m.description}" for m in metas]
    return ("## Available memory (load by name with `load_memory`)\n"
            + "\n".join(lines))


def has_memory(workspace_dir: Path | None) -> bool:
    """True iff the workspace has ANY recall content — a meaningful MEMORY.md, a
    daily note, or a typed fact. Used to gate the load_memory tool so an empty
    workspace registers no dead tool (byte-identical no-op)."""
    if workspace_dir is None:
        return False
    workspace = Path(workspace_dir)
    if not workspace.is_dir():
        return False
    root = workspace / MEMORY_FILE
    try:
        if root.is_file() and _meaningful(root.read_text(encoding="utf-8")):
            return True
    except (OSError, UnicodeDecodeError):
        pass
    mem_dir = workspace / MEMORY_DIR
    if mem_dir.is_dir():
        for path in mem_dir.glob("*.md"):
            try:
                if _meaningful(path.read_text(encoding="utf-8")):
                    return True
            except (OSError, UnicodeDecodeError):
                continue
    return False


def _resolve_fact(workspace: Path, name: str) -> Path | None:
    """Map a memory name to a file inside the workspace, or None. Tries
    memory/<name>.md then <name>.md (so 'MEMORY' loads MEMORY.md). REJECTS any
    name that could escape the workspace — the cross-persona-bleed defense."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    for rel in (f"{MEMORY_DIR}/{name}.md", f"{name}.md"):
        cand = workspace / rel
        try:
            inside = cand.resolve().is_relative_to(workspace.resolve())
        except (OSError, ValueError):
            inside = False
        if inside and cand.is_file():
            return cand
    return None


def compose_memory(workspace_dir: Path | None, names: list[str]) -> MemoryLoad:
    """Read the named memory files' bodies (each trimmed at MAX_MEMORY_CHARS) into
    one block. Mirrors skills.compose: .injected lists what was read, .skipped
    lists (name, reason). Missing/blank/unreadable => recorded, never raised."""
    load = MemoryLoad()
    if workspace_dir is None:
        for n in names:
            load.skipped.append((n, "no-workspace"))
        return load
    workspace = Path(workspace_dir)
    sections: list[str] = []
    for name in names:
        path = _resolve_fact(workspace, name)
        if path is None:
            load.skipped.append((name, "unknown"))
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            load.skipped.append((name, type(e).__name__))
            continue
        if not _meaningful(raw):
            load.skipped.append((name, "blank"))
            continue
        body, trimmed = _trim(raw, MAX_MEMORY_CHARS)
        if trimmed:
            body = body + "\n\n…[truncated]…"
        load.injected.append(name)
        sections.append(f"## {name}\n{body}")
    if load.injected:
        load.block = "\n\n".join(sections)
    return load


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
    # The typed-fact MENU makes per-fact files DISCOVERABLE — without it the agent
    # never knows a fact exists, so it never calls load_memory (the recall loop is
    # dead). Built from the manifest; content-gated like the sections.
    menu = compose_menu(load_manifest(workspace))
    if menu:
        sections.append(menu)
        load.injected.append("(manifest)")
    if load.injected:                       # CONTENT-GATED: only when something was read
        load.block = ("\n\n# Memory\n\n" + _protocol(workspace) + "\n\n"
                      + "\n\n".join(sections))
    return load
