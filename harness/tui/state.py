"""Pure presentation model for the TUI design system. Folds the existing
RenderedItem stream (render.py) + harness meta into an immutable FleetSnapshot
that dumb, reactive widgets read. No Textual, no async — exhaustively unit-
testable like render.py. See the TUI design-system spec §5."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    RESPONDING = "responding"
    RUNNING_TOOL = "running_tool"
    AWAITING_PERMISSION = "awaiting_permission"
    AWAITING_DECISION = "awaiting_decision"
    SCHEDULED = "scheduled"
    DONE = "done"
    FAILED = "failed"


class ToolStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class ToolView:
    title: str
    status: ToolStatus
    subtype: str
    body: str = ""


@dataclass(frozen=True)
class TaskItem:
    label: str
    status: str          # pending | in_progress | done | failed


@dataclass(frozen=True)
class ScheduleView:
    label: str
    when: str


@dataclass(frozen=True)
class DecisionView:
    question: str
    options: tuple[tuple[str, str], ...]   # (title, rationale)


def infer_subtype(command: str) -> str:
    """Guess a tool-call subtype from the command string, for glyph/label ONLY.
    Display concern; never asked of the engine. Neutral 'shell' fallback."""
    c = command.strip()
    if c.startswith("$ "):
        c = c[2:].strip()
    low = c.lower()
    first = low.split()[0] if low.split() else ""
    if "pytest" in low or first == "test":
        return "test"
    if first in ("sed", "apply_patch", "patch") or "apply_patch" in low:
        return "edit"
    if first in ("grep", "rg", "find", "ag"):
        return "search"
    if first in ("cat", "head", "tail", "less", "bat"):
        return "read"
    return "shell"
