"""Three-tier AGENTS.md instruction layer: compose global + project + persona
AGENTS.md into one content-gated block for the system prompt. Read-only; mirrors
memory.py's gate/trim/skip discipline. Never raises — a turn never fails on
AGENTS.md. Resolved by the dispatch caller and folded into base_block (the policy
block both the agent and chat paths consume), NOT compose_context."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path

from harness.textgate import _meaningful, _trim

logger = logging.getLogger("harness.agents")

AGENTS_FILE = "AGENTS.md"
MAX_AGENTS_CHARS = 8000          # per-tier trim cap (memory's order of magnitude)

_PREAMBLE = ("# Instructions\n\n"
             "Standing instructions for this session. When they conflict, follow "
             "persona over project over global.\n")


@dataclass
class AgentsLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)            # scope labels read
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (label, reason)


def _read_tier(dir_: Path | None, label: str, load: AgentsLoad) -> str | None:
    """Read one tier's AGENTS.md; return '## <label> instructions\\n<body>' or None
    when the dir is None/absent or the file is missing/blank/inert/unreadable.
    Records non-missing failures in load.skipped; never raises."""
    if dir_ is None:
        return None
    path = Path(dir_) / AGENTS_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        load.skipped.append((label, type(e).__name__))
        return None
    if not _meaningful(raw):
        load.skipped.append((label, "blank"))
        return None
    body, trimmed = _trim(raw, MAX_AGENTS_CHARS)
    if trimmed:
        body = body + "\n\n…[truncated]…"
    load.injected.append(label)
    return f"## {label} instructions\n{body}"


def resolve_agents(*, persona_dir: Path | None, project_cwd: Path | None,
                   global_dir: Path | None) -> AgentsLoad:
    """Compose global + project + persona AGENTS.md, content-gated, lowest-precedence
    first (so persona sits last/closest to the task). The precedence preamble is
    added only when at least one tier has content. No tier present => empty
    AgentsLoad (no block) — the byte-identical no-op."""
    load = AgentsLoad()
    sections = []
    for dir_, label in [(global_dir, "Global"), (project_cwd, "Project"),
                        (persona_dir, "Persona")]:
        section = _read_tier(dir_, label, load)
        if section is not None:
            sections.append(section)
    if load.injected:
        load.block = "\n\n" + _PREAMBLE + "\n" + "\n\n".join(sections)
    return load
