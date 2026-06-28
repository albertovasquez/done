"""LoadSkillTool: the agent pulls one skill's full body into context on demand.

Same execute->observation path as Read/Write/Edit (TracingAgent.execute_actions:
output dict -> format_observation_messages). Per-turn dedup lives on `env` (set by
the engine at the start of each turn) so a long-lived ACP session doesn't re-inject
a body the agent already loaded this turn, but CAN re-pull it on a later turn."""

from __future__ import annotations

from pathlib import Path

from harness import skills

LOAD_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": ("Load a skill's full instructions into context. Call this "
                        "before doing work a skill from the # Skills menu governs."),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string",
                               "description": "Name of the skill from the # Skills menu."}
            },
            "required": ["skill_name"],
        },
    },
}


class LoadSkillTool:
    name = "load_skill"
    schema = LOAD_SKILL_TOOL

    def __init__(self, roots: list[Path]):
        self._roots = roots
        # Fallback for envs the engine didn't stamp with a per-turn set (mock /
        # unit paths). Real runs use env._loaded_skills, reset each turn.
        self._fallback_loaded: set[str] = set()

    def display_label(self, args: dict) -> str:
        return f"load_skill {args.get('skill_name', '')}"

    def _loaded(self, env) -> set:
        loaded = getattr(env, "_loaded_skills", None)
        return loaded if loaded is not None else self._fallback_loaded

    def execute(self, args: dict, env) -> dict:
        name = args.get("skill_name", "")
        loaded = self._loaded(env)
        if name in loaded:
            return {"output": f"Skill '{name}' is already loaded this turn.",
                    "returncode": 0, "exception_info": None}
        load = skills.compose(self._roots, [name])
        if not load.injected:
            avail = skills.load_catalog(self._roots)
            names = ", ".join(m.name for m in avail) or "(none)"
            return {"output": f"Unknown skill '{name}'. Available: {names}.",
                    "returncode": 1, "exception_info": None}
        loaded.add(name)
        return {"output": load.block, "returncode": 0, "exception_info": None}
