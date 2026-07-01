"""JobsTable — dumb/reactive table for an agent's jobs. Given a tuple[JobRow],
renders TASK · STATUS · PROGRESS · ELAPSED using design-system tokens. No data
access. Progress is None in Phase 1 → renders '—' (no fabricated bars, #252)."""
from __future__ import annotations

from textual.widgets import Static

from harness.jobs.view import JobRow

# status word -> theme color token (semantic; no hardcoded hex)
_STATUS_TOKEN = {
    "RUNNING": "accent", "SCHEDULED": "scheduled", "QUEUED": "muted",
    "COMPLETED": "success", "FAILED": "error", "DISABLED": "muted",
}


# column widths (plain-text, pre-markup — Rich markup tags must never be
# counted in the padding or the rendered columns drift out of alignment)
_TASK_W, _STATUS_W, _PROGRESS_W = 32, 12, 14


def _chip(status: str) -> str:
    tok = _STATUS_TOKEN.get(status, "muted")
    return f"[${tok}][b]{status:<{_STATUS_W}}[/b][/]"


def _progress_cell(progress: float | None) -> str:
    if progress is None:
        return f"[$muted]{'—':<{_PROGRESS_W}}[/]"
    filled = int(round(progress * 20))
    bar = "█" * filled + "░" * (20 - filled)
    return f"{int(progress*100)}% [$accent]{bar}[/]"


def render_table(rows: tuple[JobRow, ...]) -> str:
    if not rows:
        return "[$muted]No jobs for this agent — nothing scheduled.[/]"
    header = f"{'TASK':<{_TASK_W}}{'STATUS':<{_STATUS_W}}{'PROGRESS':<{_PROGRESS_W}}ELAPSED"
    lines = [f"[$muted]{header}[/]"]
    for r in rows:
        name = f"[$foreground][b]{r.name:<{_TASK_W}}[/b][/]"
        desc = f"[$muted]{r.description}[/]"
        cell = f"{name}{_chip(r.status)}{_progress_cell(r.progress)}{r.elapsed}"
        lines.append(cell)
        lines.append(f"  {desc}")
    return "\n".join(lines)


class JobsTable(Static):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__("", *args, markup=True, **kwargs)
        self._rows: tuple[JobRow, ...] = ()

    def set_rows(self, rows: tuple[JobRow, ...]) -> None:
        self._rows = tuple(rows)
        self.update(render_table(self._rows))
