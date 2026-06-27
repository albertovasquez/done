"""EditTool: exact-string replace of the UNIQUE occurrence. 0 matches or >1
matches both fail (returncode 1) with no write — the model must supply enough
context to make old_string unique. Mirrors Claude Code's Edit."""

from __future__ import annotations

from pathlib import Path

EDIT_TOOL = {
    "type": "function",
    "function": {
        "name": "edit",
        "description": "Replace the unique occurrence of old_string with new_string in a file. Fails if old_string is absent or appears more than once.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (absolute, or relative to the working directory)."},
                "old_string": {"type": "string", "description": "Exact text to replace. Must be unique in the file."},
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}


class EditTool:
    name = "edit"
    schema = EDIT_TOOL

    def display_label(self, args: dict) -> str:
        return f"edit {args.get('path', '')}"

    def execute(self, args: dict, env) -> dict:
        p = Path(args["path"])
        if not p.is_absolute():
            p = Path(env.config.cwd) / p
        try:
            text = p.read_text()
        except Exception as e:
            return {"output": f"edit failed: {e}", "returncode": 1, "exception_info": None}
        count = text.count(args["old_string"])
        if count == 0:
            return {"output": "edit failed: old_string not found", "returncode": 1, "exception_info": None}
        if count > 1:
            return {"output": f"edit failed: old_string appears {count} times; add surrounding context to make it unique",
                    "returncode": 1, "exception_info": None}
        p.write_text(text.replace(args["old_string"], args["new_string"]))
        return {"output": f"edited {p}", "returncode": 0, "exception_info": None}
