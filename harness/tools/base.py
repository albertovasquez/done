"""The Tool surface: one object per tool, bundling its model-facing JSON schema
with its execution. Tools return the upstream observation shape so the existing
formatter renders them uniformly. Pure data + a callable; no I/O at import."""

from __future__ import annotations

from typing import Protocol


class Tool(Protocol):
    name: str
    schema: dict

    def display_label(self, args: dict) -> str:
        """Short human label for the 'action' trace/TUI event."""
        ...

    def execute(self, args: dict, env) -> dict:
        """Run the tool. Return {"output": str, "returncode": int,
        "exception_info": str | None}."""
        ...
