"""Pure presentation model for the TUI design system. Folds the existing
RenderedItem stream (render.py) + harness meta into an immutable FleetSnapshot
that dumb, reactive widgets read. No Textual, no async — exhaustively unit-
testable like render.py. See the TUI design-system spec §5."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    id: str = ""


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


@dataclass(frozen=True)
class AgentSnapshot:
    id: str
    name: str
    state: AgentState = AgentState.IDLE
    tool: ToolView | None = None
    activity_label: str = ""
    elapsed: float = 0.0
    tokens: int = 0
    tasks: tuple[TaskItem, ...] = ()
    schedule: ScheduleView | None = None
    decision: DecisionView | None = None


@dataclass(frozen=True)
class FleetSnapshot:
    agents: tuple[AgentSnapshot, ...]
    active_id: str

    @property
    def active(self) -> AgentSnapshot | None:
        return next((a for a in self.agents if a.id == self.active_id), None)


def initial_snapshot(agent_id: str = "default", name: str = "agent") -> FleetSnapshot:
    return FleetSnapshot(agents=(AgentSnapshot(id=agent_id, name=name),),
                         active_id=agent_id)


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


# ---- reducer events ----

@dataclass(frozen=True)
class TurnStarted: ...


@dataclass(frozen=True)
class TurnEnded:
    ok: bool = True


@dataclass(frozen=True)
class ItemReceived:
    item: object              # a render.RenderedItem (duck-typed: .kind/.title/.status/.id)


@dataclass(frozen=True)
class TokensUpdated:
    total: int


@dataclass(frozen=True)
class PermissionOpened: ...


@dataclass(frozen=True)
class PermissionClosed: ...


@dataclass(frozen=True)
class DecisionOpened:
    view: "DecisionView"


def _tool_status(raw: str) -> ToolStatus:
    s = str(raw)
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return {
        "pending": ToolStatus.PENDING,
        "in_progress": ToolStatus.ACTIVE,
        "active": ToolStatus.ACTIVE,
        "completed": ToolStatus.DONE,
        "failed": ToolStatus.FAILED,
    }.get(s, ToolStatus.ACTIVE)


def _task_status_from_tool(ts: ToolStatus) -> str:
    return {ToolStatus.DONE: "done", ToolStatus.FAILED: "failed"}.get(ts, "in_progress")


def _reduce_agent(a: AgentSnapshot, event) -> AgentSnapshot:
    if isinstance(event, TurnStarted):
        return replace(a, state=AgentState.THINKING, activity_label="Thinking…",
                       tool=None, decision=None, tasks=(), elapsed=0.0)
    if isinstance(event, TokensUpdated):
        return replace(a, tokens=event.total)
    if isinstance(event, PermissionOpened):
        return replace(a, state=AgentState.AWAITING_PERMISSION)
    if isinstance(event, PermissionClosed):
        nxt = AgentState.RUNNING_TOOL if a.tool is not None else AgentState.RESPONDING
        return replace(a, state=nxt)
    if isinstance(event, DecisionOpened):
        return replace(a, state=AgentState.AWAITING_DECISION, decision=event.view)
    if isinstance(event, TurnEnded):
        return replace(a, state=AgentState.DONE if event.ok else AgentState.FAILED,
                       tool=None, activity_label="")
    if isinstance(event, ItemReceived):
        item = event.item
        kind = getattr(item, "kind", "")
        if kind == "message":
            return replace(a, state=AgentState.RESPONDING, activity_label="Responding…")
        if kind == "tool":
            ts = _tool_status(getattr(item, "status", ""))
            title = getattr(item, "title", "")
            subtype = infer_subtype(title)
            tool = ToolView(title=title, status=ts, subtype=subtype)
            tasks = a.tasks + (TaskItem(label=title, status="in_progress"),)
            return replace(a, state=AgentState.RUNNING_TOOL, tool=tool, tasks=tasks,
                           activity_label=f"Running {subtype}")
        if kind == "tool_update":
            ts = _tool_status(getattr(item, "status", ""))
            body = getattr(item, "body", "") or (a.tool.body if a.tool is not None else "")
            tool = replace(a.tool, status=ts, body=body) if a.tool is not None else None
            new_task_status = _task_status_from_tool(ts)
            live_title = a.tool.title if a.tool is not None else None
            if live_title is not None and any(t.label == live_title for t in a.tasks):
                # Match by the live tool's title (correct: not sensitive to task order)
                matched = False
                new_tasks = []
                for t in reversed(a.tasks):
                    if not matched and t.label == live_title:
                        new_tasks.append(replace(t, status=new_task_status))
                        matched = True
                    else:
                        new_tasks.append(t)
                tasks = tuple(reversed(new_tasks))
            else:
                # Defensive fallback: no match or no live tool — update the last task
                tasks = tuple(
                    replace(t, status=new_task_status) if i == len(a.tasks) - 1 else t
                    for i, t in enumerate(a.tasks)
                ) if a.tasks else a.tasks
            return replace(a, tool=tool, tasks=tasks)
    return a


def reduce(snapshot: FleetSnapshot, event) -> FleetSnapshot:
    """Pure: fold one event into the snapshot, updating the ACTIVE agent only
    (single-agent today; fleet fan-out later targets event.agent_id)."""
    agents = tuple(
        _reduce_agent(a, event) if a.id == snapshot.active_id else a
        for a in snapshot.agents
    )
    return FleetSnapshot(agents=agents, active_id=snapshot.active_id)


def decision_from_meta(field_meta: dict | None) -> DecisionView | None:
    """Recognize a clarification ('grill-me') request from the harness meta chip.
    Tolerant: any missing/malformed shape yields None, never raises. Swaps to a
    formal ACP signal later with no widget change (spec §5.1)."""
    if not isinstance(field_meta, dict):
        return None
    harness = field_meta.get("harness")
    if not isinstance(harness, dict):
        return None
    dec = harness.get("decision")
    if not isinstance(dec, dict):
        return None
    question = dec.get("question")
    raw_opts = dec.get("options")
    if not question or not isinstance(raw_opts, list) or not raw_opts:
        return None
    options = tuple(
        (str(o.get("title", "")), str(o.get("rationale", "")))
        for o in raw_opts if isinstance(o, dict) and o.get("title")
    )
    if not options:
        return None
    return DecisionView(question=str(question), options=options)
