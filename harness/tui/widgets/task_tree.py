"""TaskTree — the live checklist (✓ done / ▣ in-progress / □ pending / ✗ failed),
updated in place. Reads a tuple of TaskItem. See spec §6 / components.md C."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import TaskItem
from harness.tui.tokens import GLYPH

# GLYPH has no task-status glyph for "in_progress" or "pending"; keep literals.
_GLYPH = {
    "done": (GLYPH["done"], "success"),
    "failed": (GLYPH["failed"], "error"),
    "in_progress": ("▣", "accent"),
    "pending": ("□", "muted"),
}


class TaskTree(Static):
    def __init__(self) -> None:
        super().__init__(markup=True)

    def lines_for(self, tasks: tuple[TaskItem, ...]) -> list[str]:
        out = []
        for t in tasks:
            glyph, token = _GLYPH.get(t.status, ("□", "muted"))
            label = t.label[2:] if t.label.startswith("$ ") else t.label
            out.append(f"[${token}]{glyph}[/] [$foreground]{label}[/]")
        return out

    def update_tasks(self, tasks: tuple[TaskItem, ...]) -> None:
        self.update("\n".join(self.lines_for(tasks)))
