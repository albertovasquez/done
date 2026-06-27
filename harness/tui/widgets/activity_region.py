"""ActivityRegion — the pinned, transient zone above the composer that shows what
the agent is doing RIGHT NOW. Tool calls live here, NOT in the transcript scroll.
Compact while working (status line + task checklist); ctrl+o expands to per-tool
detail; renders empty when idle/terminal. Reads an AgentSnapshot. See spec §3.

Mount strategy: TaskTree and the tools container are ALWAYS mounted in compose()
and toggled via `.display`. This avoids async mount-timing races that arise when
remove_children()/mount() are called fire-and-forget inside the sync update_from()
method. The tools container is repopulated synchronously via remove_children() +
mount() only for ToolCallRow children — a bounded, low-count operation that Textual
queues in stable order."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from harness.tui.state import AgentSnapshot, AgentState
from harness.tui.widgets.activity_status import ActivityStatus
from harness.tui.widgets.task_tree import TaskTree
from harness.tui.widgets.tool_call_row import ToolCallRow

_WORKING = {AgentState.THINKING, AgentState.RESPONDING, AgentState.RUNNING_TOOL,
            AgentState.AWAITING_PERMISSION, AgentState.AWAITING_DECISION}


class ActivityRegion(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._details = False
        self._snap: AgentSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="ar-rule", markup=True)
        yield ActivityStatus(id="ar-status")
        yield TaskTree(id="ar-tasks")
        yield Vertical(id="ar-tools")   # holds ToolCallRow children when expanded

    def is_idle(self, snap: AgentSnapshot) -> bool:
        return snap is None or snap.state not in _WORKING

    def toggle_details(self) -> None:
        self._details = not self._details
        if self._snap is not None:
            self.update_from(self._snap)

    def update_from(self, snap: AgentSnapshot) -> None:
        self._snap = snap
        idle = self.is_idle(snap)
        self.display = not idle

        self.query_one("#ar-status", ActivityStatus).update_from(snap)
        self.query_one("#ar-rule", Static).update(
            "" if idle else "[$muted]" + "─" * 40 + "[/]")

        task_tree = self.query_one("#ar-tasks", TaskTree)
        tools_container = self.query_one("#ar-tools", Vertical)

        if idle:
            task_tree.display = False
            tools_container.display = False
            return

        show_tools = self._details and bool(snap.tools)
        task_tree.display = not show_tools
        tools_container.display = show_tools

        if not show_tools:
            task_tree.update_tasks(snap.tasks)
        else:
            tools_container.remove_children()
            for tv in snap.tools:
                tools_container.mount(ToolCallRow(tv, expanded=True))
