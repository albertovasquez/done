"""build_registry(): the live tool list for one agent construction. FRESH list
per call — never a module-global — because multiple model instances (worker vs.
chat, per-persona) must not share mutable tool state.

When skill_roots are passed, the agent gets a load_skill tool so it can pull skill
bodies on demand (lazy discovery). With no roots, the registry is exactly the four
default tools — a strict no-op for any caller that doesn't opt in."""

from __future__ import annotations

from pathlib import Path

from harness.tools.base import Tool
from harness.tools.bash import BashTool
from harness.tools.edit import EditTool
from harness.tools.load_skill import LoadSkillTool
from harness.tools.read import ReadTool
from harness.tools.write import WriteTool


def build_registry(skill_roots: list[Path] | None = None) -> list[Tool]:
    tools: list[Tool] = [BashTool(), ReadTool(), WriteTool(), EditTool()]
    if skill_roots:
        tools.append(LoadSkillTool(skill_roots))
    return tools
