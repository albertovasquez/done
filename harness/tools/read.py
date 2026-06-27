"""ReadTool: whole-file read. Whole file only (no offset/limit — bash sed -n
covers ranges). Errors surface as returncode=1, matching a failed shell read,
so the model reacts the same way it does to bash failures."""

from __future__ import annotations

from pathlib import Path

READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a text file and return its full contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (absolute, or relative to the working directory)."}
            },
            "required": ["path"],
        },
    },
}


class ReadTool:
    name = "read"
    schema = READ_TOOL

    def display_label(self, args: dict) -> str:
        return f"read {args.get('path', '')}"

    def execute(self, args: dict, env) -> dict:
        p = Path(args["path"])
        if not p.is_absolute():
            p = Path(env.config.cwd) / p
        try:
            return {"output": p.read_text(), "returncode": 0, "exception_info": None}
        except Exception as e:
            return {"output": f"read failed: {e}", "returncode": 1, "exception_info": None}
