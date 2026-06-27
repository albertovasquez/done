"""BashTool: schema only. Bash is never dispatched via execute(); the agent
routes it through env.execute so the environment's Submitted-on-completion
mechanism stays intact. execute() therefore raises if ever called."""

from __future__ import annotations

from minisweagent.models.utils.actions_toolcall import BASH_TOOL


class BashTool:
    name = "bash"
    schema = BASH_TOOL

    def display_label(self, args: dict) -> str:
        return args.get("command", "")

    def execute(self, args: dict, env) -> dict:
        raise NotImplementedError("bash is dispatched via env.execute, not Tool.execute")
