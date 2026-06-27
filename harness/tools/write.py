"""WriteTool: create or overwrite a file. Raw write — the 'look before you
overwrite' rule stays prompt-level guidance; a hard read-gate needs read-tracking
state dn does not have yet (deferred)."""

from __future__ import annotations

from pathlib import Path

WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "write",
        "description": "Create or overwrite a text file with the given content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (absolute, or relative to the working directory)."},
                "content": {"type": "string", "description": "Full file content to write."},
            },
            "required": ["path", "content"],
        },
    },
}


class WriteTool:
    name = "write"
    schema = WRITE_TOOL

    def display_label(self, args: dict) -> str:
        return f"write {args.get('path', '')}"

    def execute(self, args: dict, env) -> dict:
        p = Path(args["path"])
        if not p.is_absolute():
            p = Path(env.config.cwd) / p
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return {"output": f"wrote {p}", "returncode": 0, "exception_info": None}
        except Exception as e:
            return {"output": f"write failed: {e}", "returncode": 1, "exception_info": None}
