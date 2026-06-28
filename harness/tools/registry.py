"""build_registry(): the live tool list for one agent construction. FRESH list
per call — never a module-global — because multiple model instances (worker vs.
chat, per-persona) must not share mutable tool state.

When skill_roots are passed, the agent gets a load_skill tool so it can pull skill
bodies on demand (lazy discovery). When a memory_root (the session workspace) is
passed, it gets a load_memory tool so it can pull remembered facts on demand. With
neither, the registry is exactly the four default tools — a strict no-op for any
caller that doesn't opt in."""

from __future__ import annotations

from pathlib import Path

from harness import memory as memory_mod
from harness.tools.base import Tool
from harness.tools.bash import BashTool
from harness.tools.edit import EditTool
from harness.tools.load_memory import LoadMemoryTool
from harness.tools.load_skill import LoadSkillTool
from harness.tools.read import ReadTool
from harness.tools.write import WriteTool


def build_registry(skill_roots: list[Path] | None = None,
                   memory_root: Path | None = None) -> list[Tool]:
    tools: list[Tool] = [BashTool(), ReadTool(), WriteTool(), EditTool()]
    if skill_roots:
        tools.append(LoadSkillTool(skill_roots))
    # Gate load_memory on the workspace actually HAVING recall content — an empty
    # workspace must not advertise a dead tool (byte-identical no-op).
    if memory_root and memory_mod.has_memory(memory_root):
        tools.append(LoadMemoryTool(memory_root))
    return tools
