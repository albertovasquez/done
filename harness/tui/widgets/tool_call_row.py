"""ToolCallRow — one tool call: subtype glyph + title + status chip. Reads a
ToolView. Subtype glyph is inferred (display-only). See spec §6 / components.md C."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import ToolView
from harness.tui.tokens import GLYPH
from harness.tui.widgets.status_chip import TOOL_STATUS_TOKEN, TOOL_STATUS_LABEL


class ToolCallRow(Static):
    def __init__(self, tool: ToolView) -> None:
        super().__init__(markup=True)
        self._tool = tool
        self.update(self.line_for(tool))

    def line_for(self, tool: ToolView) -> str:
        glyph = GLYPH.get(tool.subtype, GLYPH["shell"])
        title = tool.title[2:] if tool.title.startswith("$ ") else tool.title
        token = TOOL_STATUS_TOKEN.get(tool.status, "muted")
        label = TOOL_STATUS_LABEL.get(tool.status, "")
        return (f"[${token}]{glyph}[/] [$foreground]{title}[/]   "
                f"[${token}][b]{label}[/b][/]")
