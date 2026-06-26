"""ToolCallRow — one tool call inside the pinned ActivityRegion: subtype glyph +
title + status chip (collapsed), plus a tailored, capped body when expanded.
Reads a ToolView. Subtype glyph is inferred (display-only). See spec §3."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import ToolView
from harness.tui.tokens import GLYPH
from harness.tui.widgets.status_chip import TOOL_STATUS_TOKEN, TOOL_STATUS_LABEL

_CAP = {"read": 6}        # per-subtype line cap; default below
_DEFAULT_CAP = 10


def cap_body(body: str, subtype: str) -> str:
    """Truncate a tool's output to a per-subtype line cap. Pure/display-only."""
    if not body:
        return ""
    cap = _CAP.get(subtype, _DEFAULT_CAP)
    lines = body.splitlines()
    if len(lines) <= cap:
        return "\n".join(lines)
    return "\n".join(lines[:cap] + [f"… (+{len(lines) - cap} more lines)"])


class ToolCallRow(Static):
    def __init__(self, tool: ToolView, expanded: bool = False) -> None:
        super().__init__(markup=True)
        self._tool = tool
        self._expanded = expanded
        self.update(self.detail_for(tool) if expanded else self.line_for(tool))

    def line_for(self, tool: ToolView) -> str:
        glyph = GLYPH.get(tool.subtype, GLYPH["shell"])
        title = tool.title[2:] if tool.title.startswith("$ ") else tool.title
        token = TOOL_STATUS_TOKEN.get(tool.status, "muted")
        label = TOOL_STATUS_LABEL.get(tool.status, "")
        return (f"[${token}]{glyph}[/] [$foreground]{title}[/]   "
                f"[${token}][b]{label}[/b][/]")

    def detail_for(self, tool: ToolView) -> str:
        head = self.line_for(tool)
        body = cap_body(tool.body, tool.subtype)
        if not body:
            return head
        escaped = body.replace("[", "\\[")
        return f"{head}\n[$code]{escaped}[/]"
