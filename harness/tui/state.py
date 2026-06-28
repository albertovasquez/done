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
    if first == "read":          # the dedicated Read tool's display label
        return "read"
    if first in ("write", "edit"):   # Write/Edit tools → ✎ (closest shipped glyph)
        return "edit"
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
    view: "DecisionView | None"   # None clears an open decision


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


# The agent ends every turn by running this exact command to signal completion
# (see acp_agent.py / acp_emit.py, which titles the tool call "$ <command>"). It
# is a protocol artifact, not user-facing work, so we drop it from the activity
# region. We match ONLY this exact command — any other echo the agent runs is
# real work and renders normally.
_DONE_SENTINEL_CMD = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def _is_done_sentinel(title: str) -> bool:
    """True when a tool title is the turn-completion sentinel. Titles arrive as
    "$ <command>"; we strip that prefix before the exact-match comparison."""
    cmd = title[2:] if title.startswith("$ ") else title
    return cmd.strip() == _DONE_SENTINEL_CMD


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
        if event.view is None:   # clear: leave the awaiting-decision state
            nxt = AgentState.RUNNING_TOOL if a.tool is not None else AgentState.RESPONDING
            return replace(a, state=nxt, decision=None)
        return replace(a, state=AgentState.AWAITING_DECISION, decision=event.view)
    if isinstance(event, TurnEnded):
        return replace(a, state=AgentState.DONE if event.ok else AgentState.FAILED,
                       tool=None, activity_label="")
    if isinstance(event, ItemReceived):
        # TurnEnded is TERMINAL for the activity state. A late session_update
        # notification (message/tool chunk) can drain on Textual's queue AFTER the
        # prompt() RPC response already resolved and applied TurnEnded — the two
        # channels have no app-level ordering guarantee. Without this guard such a
        # straggler re-sets RESPONDING/RUNNING_TOOL with no following TurnEnded to
        # clear it, so the ActivityRegion sticks on "Responding (elapsed)" forever
        # (the reported stuck-spinner bug). Once terminal, no item advances the
        # activity state. Prose still renders: that path is app._stream_message,
        # independent of this reducer.
        if a.state in (AgentState.DONE, AgentState.FAILED):
            return a
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
            title = getattr(item, "title", "")
            if _is_done_sentinel(title):
                return a            # protocol artifact — never render the turn-end echo
            ts = _tool_status(getattr(item, "status", ""))
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
        # Select the agent whose id == event.id as active. If it already exists,
        # just point active_id at it (preserve its state — do NOT rename whoever
        # was active). Only seed a fresh agent when no agent carries that id.
        # This makes "rename in place" obsolete: switching is selection.
        if any(a.id == event.id for a in snapshot.agents):
            return FleetSnapshot(agents=snapshot.agents, active_id=event.id)
        return FleetSnapshot(
            agents=snapshot.agents + (AgentSnapshot(id=event.id, name=event.id),),
            active_id=event.id)
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
