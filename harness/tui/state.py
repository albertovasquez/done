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
