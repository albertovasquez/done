"""build_registry(): the live tool list for one agent construction. FRESH list
per call — never a module-global — because multiple model instances (worker vs.
chat, per-persona) must not share mutable tool state.

When skill_roots are passed, the agent gets a load_skill tool so it can pull skill
bodies on demand (lazy discovery). When a memory_root (the session workspace) is
passed, it gets a load_memory tool so it can pull remembered facts on demand. With
neither, the registry is the five always-present tools (bash, read, write, edit,
create_job) — load_skill/load_memory are the only context-gated additions."""

from __future__ import annotations

from pathlib import Path

from harness import memory as memory_mod
from harness.tools.base import Tool
from harness.tools.bash import BashTool
from harness.tools.create_job import CreateJobTool
from harness.tools.edit import EditTool
from harness.tools.load_memory import LoadMemoryTool
from harness.tools.load_skill import LoadSkillTool
from harness.tools.read import ReadTool
from harness.tools.write import WriteTool


def build_registry(skill_roots: list[Path] | None = None,
                   memory_root: Path | None = None,
                   *,
                   toolset: set[str] | None = None,
                   is_worker: bool = False) -> list[Tool]:
    # CreateJobTool is always present (needs no roots/context) — it is the agent's
    # ONLY way to actually create a cron job after the create-job gates; without it
    # the model loops re-asking the gates.
    # Local import breaks the cycle: subagent → agent_build → registry → subagent.
    from harness.tools.subagent import SubagentTool  # noqa: PLC0415
    tools: list[Tool] = [BashTool(), ReadTool(), WriteTool(), EditTool(), CreateJobTool(),
                         SubagentTool()]
    if skill_roots:
        tools.append(LoadSkillTool(skill_roots))
    # Gate load_memory on the workspace actually HAVING recall content — an empty
    # workspace must not advertise a dead tool (byte-identical no-op).
    if memory_root and memory_mod.has_memory(memory_root):
        tools.append(LoadMemoryTool(memory_root))
    # Depth-1 enforcement: a worker can NEVER call subagent (explicit deny, not a
    # side effect of the toolset — a task could name it in `tools`).
    if is_worker:
        tools = [t for t in tools if t.name != "subagent"]
    # Restricted toolset: keep only the named tools (model schemas AND agent
    # dispatch use this one list, so they always agree).
    if toolset is not None:
        tools = [t for t in tools if t.name in toolset]
    return tools
