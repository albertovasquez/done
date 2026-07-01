"""ActivityRegion — the pinned, transient zone above the composer that shows what
the agent is doing RIGHT NOW. Tool calls live here, NOT in the transcript scroll.
While working, shows only the status line (which carries a '· N done' tool
count); ctrl+o switches to a scannable per-tool list (one-line heads: glyph +
title + status chip); renders empty when idle/terminal. The TaskTree widget shows
the agent's plan checklist when the snapshot carries one (snap.plan), else stays
hidden. Reads an AgentSnapshot. See spec §3.

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

from harness.tui.fmt import fmt_elapsed, fmt_tokens_lower
from harness.tui.state import AgentSnapshot, AgentState, WorkerSummary
from harness.tui.widgets.activity_status import ActivityStatus
from harness.tui.widgets.task_tree import TaskTree
from harness.tui.widgets.tool_call_row import ToolCallRow

_WORKING = {AgentState.THINKING, AgentState.RESPONDING, AgentState.RUNNING_TOOL,
            AgentState.AWAITING_PERMISSION, AgentState.AWAITING_DECISION}

_WORKER_GLYPH = {"pending": "⏱", "running": "◐", "done": "✓", "failed": "✗"}
_WORKER_TOKEN = {"pending": "scheduled", "running": "accent",
                 "done": "success", "failed": "error"}


def worker_summary_line(s: WorkerSummary) -> str:
    """The one-line transcript record left when a worker batch finishes."""
    n = s.ok + s.failed
    fail = f" [$error]· {s.failed} failed[/]" if s.failed else ""
    return (f"[$success]✓[/] {n} workers{fail} "
            f"[$muted]· {fmt_elapsed(s.total_elapsed)} · ↓ {fmt_tokens_lower(s.total_tokens)} tokens[/]")


class ActivityRegion(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._details = False
        self._snap: AgentSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield ActivityStatus(id="ar-status")
        yield Static(id="ar-workers", markup=True)   # live worker card (subagent batch)
        yield TaskTree(id="ar-tasks")
        yield Vertical(id="ar-tools")   # holds one-line ToolCallRow children (ctrl+o)

    def is_idle(self, snap: AgentSnapshot) -> bool:
        return snap is None or snap.state not in _WORKING

    @staticmethod
    def show_plan(snap: AgentSnapshot) -> bool:
        return snap is not None and snap.state in _WORKING and bool(snap.plan)

    @staticmethod
    def show_workers(snap: AgentSnapshot) -> bool:
        return snap is not None and bool(snap.workers)

    @staticmethod
    def worker_lines(snap: AgentSnapshot) -> list[str]:
        """Header + one row per live worker. Running rows show a spinner glyph;
        finished rows show their elapsed + tokens. Elapsed does NOT tick per-row
        (the worker's monotonic clock lives in the agent process, a different
        clock domain than this TUI) — running rows read 'working…' and settle to
        the agent-reported elapsed on completion."""
        rows = snap.workers
        n = len(rows)
        header = f"[$accent]●[/] [b]{n} workers[/b] [$muted]running[/]"
        lines = [header]
        for w in rows:
            glyph = _WORKER_GLYPH.get(w.status, "◐")
            tok = _WORKER_TOKEN.get(w.status, "accent")
            goal = w.goal if len(w.goal) <= 40 else w.goal[:39] + "…"
            if w.status in ("done", "failed"):
                meta = f"{fmt_elapsed(w.elapsed)} · ↓ {fmt_tokens_lower(w.tokens)}"
            else:
                meta = "working…" if w.tokens == 0 else f"↓ {fmt_tokens_lower(w.tokens)}"
            lines.append(f" [{tok}]{glyph}[/] {goal}  [$muted]{meta}[/]")
        return lines

    def toggle_details(self) -> None:
        self._details = not self._details
        if self._snap is not None:
            self.update_from(self._snap)

    def update_from(self, snap: AgentSnapshot) -> None:
        self._snap = snap
        idle = self.is_idle(snap)
        self.display = not idle

        self.query_one("#ar-status", ActivityStatus).update_from(snap)

        task_tree = self.query_one("#ar-tasks", TaskTree)
        tools_container = self.query_one("#ar-tools", Vertical)
        workers = self.query_one("#ar-workers", Static)

        if idle:
            task_tree.display = False
            tools_container.display = False
            workers.display = False
            return

        show_workers = self.show_workers(snap)
        workers.display = show_workers
        if show_workers:
            workers.update("\n".join(self.worker_lines(snap)))

        show_tools = self._details and bool(snap.tools)
        show_plan = self.show_plan(snap)
        # Default view = status line + plan checklist (when the agent emitted one).
        # The status line carries '· N done'; ctrl+o reveals the per-tool list.
        task_tree.display = show_plan
        tools_container.display = show_tools

        if show_plan:
            task_tree.update_tasks(snap.plan)

        if show_tools:
            tools_container.remove_children()
            for tv in snap.tools:
                tools_container.mount(ToolCallRow(tv, expanded=False))
