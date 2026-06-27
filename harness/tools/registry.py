"""build_registry(): the live tool list for one agent construction. FRESH list
per call — never a module-global — because multiple model instances (worker vs.
chat, per-persona) must not share mutable tool state."""

from __future__ import annotations

from harness.tools.base import Tool
from harness.tools.bash import BashTool


def build_registry() -> list[Tool]:
    return [BashTool()]
