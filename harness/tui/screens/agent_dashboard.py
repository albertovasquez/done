"""AgentDashboard — a per-agent, jobs-first activity screen. Header (name·state)
+ JobsTable fed from the pure view model. esc closes. Progress is None in P1."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from harness.jobs.view import job_rows
from harness.tui.widgets.jobs_table import JobsTable


class AgentDashboard(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, agent_id: str, agent_name: str, agent_state: str = "") -> None:
        super().__init__()
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._agent_state = agent_state

    def compose(self) -> ComposeResult:
        header = self._agent_name
        if self._agent_state:
            header = f"{self._agent_name} · {self._agent_state}"
        with Vertical(id="agent-dashboard"):
            yield Static(
                f"[$muted]ACTIVE AGENT[/]\n[$accent][b]{header}[/b][/]",
                id="dashboard-header", markup=True)
            yield JobsTable(id="dashboard-jobs")

    def on_mount(self) -> None:
        rows = job_rows(self._agent_id, now=time.time())
        self.query_one("#dashboard-jobs", JobsTable).set_rows(rows)
