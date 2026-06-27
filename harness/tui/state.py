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
    tool_id: str = ""    # the tool_call_id this row tracks (match updates by id, not label)


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
    plan: tuple[TaskItem, ...] = ()
    tools: tuple[ToolView, ...] = ()
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


@dataclass(frozen=True)
class PersonaResolved:
    id: str


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


def _plan_task_status(raw: str) -> str:
    return {"pending": "pending", "in_progress": "in_progress",
            "completed": "done"}.get(str(raw), "pending")


def _reduce_agent(a: AgentSnapshot, event) -> AgentSnapshot:
    if isinstance(event, TurnStarted):
        return replace(a, state=AgentState.THINKING, activity_label="Thinking",
                       tool=None, decision=None, tasks=(), tools=(), plan=(),
                       elapsed=0.0)
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
            return replace(a, state=AgentState.RESPONDING, activity_label="Responding")
        if kind == "plan":
            entries = getattr(item, "entries", ()) or ()
            plan = tuple(
                TaskItem(label=content, status=_plan_task_status(status), tool_id="")
                for content, status in entries
            )
            return replace(a, plan=plan)
        if kind == "tool":
            ts = _tool_status(getattr(item, "status", ""))
            title = getattr(item, "title", "")
            tid = getattr(item, "id", "")
            subtype = infer_subtype(title)
            tool = ToolView(title=title, status=ts, subtype=subtype, id=tid)
            tasks = a.tasks + (TaskItem(label=title, status="in_progress", tool_id=tid),)
            tools = a.tools + (tool,)
            return replace(a, state=AgentState.RUNNING_TOOL, tool=tool,
                           tasks=tasks, tools=tools, activity_label=f"Running {subtype}")
        if kind == "tool_update":
            ts = _tool_status(getattr(item, "status", ""))
            uid = getattr(item, "id", "")
            # Match by id. An empty update id is ambiguous (every default-id tool
            # would match), so a blank uid matches nothing — no-op rather than
            # clobber unrelated rows.
            def _match(tid: str) -> bool:
                return bool(uid) and tid == uid
            new_tools = tuple(
                replace(tv, status=ts, body=(getattr(item, "body", "") or tv.body))
                if _match(tv.id) else tv
                for tv in a.tools
            )
            updated = next((tv for tv in new_tools if _match(tv.id)), None)
            new_task_status = _task_status_from_tool(ts)
            new_tasks = tuple(
                replace(t, status=new_task_status) if _match(t.tool_id) else t
                for t in a.tasks
            )
            live = updated if updated is not None else a.tool
            return replace(a, tool=live, tools=new_tools, tasks=new_tasks)
    return a


def reduce(snapshot: FleetSnapshot, event) -> FleetSnapshot:
    """Pure: fold one event into the snapshot, updating the ACTIVE agent only
    (single-agent today; fleet fan-out later targets event.agent_id)."""
    if isinstance(event, PersonaResolved):
        # Set the active persona id and rename the (single) active agent to it.
        # C2a is single-agent: remap the active snapshot's id+name to the persona.
        # (C2b reads active_id to highlight; C2c grows the tuple per real session.)
        agents = tuple(
            replace(a, id=event.id, name=event.id) if a.id == snapshot.active_id else a
            for a in snapshot.agents
        )
        # Invariant: after PersonaResolved, active_id MUST resolve to an agent.
        # If the old active_id matched none (empty tuple, or active was already None —
        # reachable once C2c holds multiple agents), seed one so .active is never None.
        if not any(a.id == event.id for a in agents):
            agents = agents + (AgentSnapshot(id=event.id, name=event.id),)
        return FleetSnapshot(agents=agents, active_id=event.id)
    agents = tuple(
        _reduce_agent(a, event) if a.id == snapshot.active_id else a
        for a in snapshot.agents
    )
    return FleetSnapshot(agents=agents, active_id=snapshot.active_id)


def persona_from_meta(field_meta: dict | None) -> str | None:
    """Recognize the active persona id from the harness meta chip.
    Tolerant: any missing/malformed shape yields None, never raises."""
    if not isinstance(field_meta, dict):
        return None
    harness = field_meta.get("harness")
    if not isinstance(harness, dict):
        return None
    persona = harness.get("persona")
    if not isinstance(persona, dict):
        return None
    pid = persona.get("id")
    return pid if isinstance(pid, str) and pid else None


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
