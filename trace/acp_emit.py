"""Pure builders for ACP session/update objects. The only module that knows
ACP's update shapes; acp_env/acp_agent call these. No JSON-RPC, no I/O —
unit-testable in isolation."""

from __future__ import annotations

from typing import Any

from acp import (
    start_tool_call,
    update_tool_call,
    tool_content,
    text_block,
    update_agent_message_text,
)


def tool_call_start(tool_call_id: str, command: str):
    return start_tool_call(tool_call_id, f"$ {command}", kind="execute", status="pending")


def tool_call_done(tool_call_id: str, output: dict):
    status = "completed" if output.get("returncode", -1) == 0 else "failed"
    body = output.get("output", "") or output.get("exception_info", "") or "(no output)"
    return update_tool_call(tool_call_id, status=status,
                            content=[tool_content(text_block(body))])


def message_chunk(text: str):
    return update_agent_message_text(text)


def with_meta(update, harness_meta: dict[str, Any]):
    existing = update.field_meta or {}
    update.field_meta = {**existing, "harness": harness_meta}
    return update
