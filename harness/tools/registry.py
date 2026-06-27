"""build_registry(): the live tool list for one agent construction. FRESH list
per call — never a module-global — because multiple model instances (worker vs.
chat, per-persona) must not share mutable tool state."""

from __future__ import annotations

from harness.tools.base import Tool
from harness.tools.bash import BashTool
from harness.tools.edit import EditTool
from harness.tools.read import ReadTool
from harness.tools.write import WriteTool


def build_registry() -> list[Tool]:
    return [BashTool(), ReadTool(), WriteTool(), EditTool()]
