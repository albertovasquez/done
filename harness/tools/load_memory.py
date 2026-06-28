"""LoadMemoryTool: the agent pulls one memory fact's full body into context on
demand. The recall half of the memory system — `resolve_memory` injects the
manifest/index at startup; this tool fetches a fact the agent didn't get in that
opening dump (older daily notes, a fact trimmed out of the startup block, any
typed fact under memory/).

Structural clone of LoadSkillTool: same execute->observation path
(TracingAgent.execute_actions: output dict -> format_observation_messages), same
per-turn dedup (env._loaded_memories, reset by the engine each turn) so a
long-lived ACP session doesn't re-inject a body the agent already loaded this
turn but CAN re-pull it on a later turn. Names are resolved STRICTLY inside the
session workspace (compose_memory._resolve_fact) — the cross-persona-bleed
defense."""

from __future__ import annotations

from pathlib import Path

from harness import memory

LOAD_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "load_memory",
        "description": ("Load a remembered fact's full text into context. Call this "
                        "before acting on something the # Memory manifest references "
                        "but didn't include in full."),
        "parameters": {
            "type": "object",
            "properties": {
                "memory_name": {"type": "string",
                                "description": "Name of the fact from the # Memory manifest."}
            },
            "required": ["memory_name"],
        },
    },
}


class LoadMemoryTool:
    name = "load_memory"
    schema = LOAD_MEMORY_TOOL

    def __init__(self, workspace_dir: Path):
        self._workspace = Path(workspace_dir)
        # Fallback for envs the engine didn't stamp with a per-turn set (mock /
        # unit paths). Real runs use env._loaded_memories, reset each turn.
        self._fallback_loaded: set[str] = set()

    def display_label(self, args: dict) -> str:
        return f"load_memory {args.get('memory_name', '')}"

    def _loaded(self, env) -> set:
        loaded = getattr(env, "_loaded_memories", None)
        return loaded if loaded is not None else self._fallback_loaded

    def execute(self, args: dict, env) -> dict:
        name = args.get("memory_name", "")
        loaded = self._loaded(env)
        if name in loaded:
            return {"output": f"Memory '{name}' is already loaded this turn.",
                    "returncode": 0, "exception_info": None}
        load = memory.compose_memory(self._workspace, [name])
        if not load.injected:
            avail = ", ".join(m.name for m in memory.load_manifest(self._workspace)) or "(none)"
            return {"output": f"Unknown memory '{name}'. Available: {avail}.",
                    "returncode": 1, "exception_info": None}
        loaded.add(name)
        return {"output": load.block, "returncode": 0, "exception_info": None}
