"""Pure builders for ACP session/update objects. The only module that knows
ACP's update shapes; acp_env/acp_agent call these. No JSON-RPC, no I/O —
unit-testable in isolation."""

from __future__ import annotations

import shlex
from typing import Any

from acp import (
    start_tool_call,
    update_tool_call,
    tool_content,
    text_block,
    update_agent_message_text,
    update_user_message_text,
    update_plan,
    plan_entry,
)

# The sentinel command the agent runs to publish a plan. The harness intercepts
# it, emits an ACP plan update, and does NOT execute it as a shell command — the
# same "structured capability over the bash-only channel" pattern memory uses.
_PLAN_STATUSES = {"pending", "in_progress", "completed"}
# Bare tokens shlex.split leaves behind for unquoted shell control. Their
# presence among a `plan` line's args means a real command was chained onto the
# sentinel, so it is not a pure plan.
_SHELL_OPERATORS = {"&&", "||", "|", ";", "&", ">", ">>", "<", "<<", "<<<"}


def _is_shell_chain_token(tok: str) -> bool:
    """True if a `plan` arg token signals a chained real command. shlex.split
    yields operators in a few shapes: standalone (`&&`, `|`, `;`, `>`), a heredoc
    redirect glued to its tag (`<<PY`), or a `;`/`&` glued to the previous word
    (`Step;`). None can occur inside a properly quoted `label:status` arg."""
    return (tok in _SHELL_OPERATORS
            or tok.startswith("<<")
            or tok.endswith((";", "&")))


def tool_call_start(tool_call_id: str, command: str):
    return start_tool_call(tool_call_id, f"$ {command}", kind="execute", status="pending")


def tool_call_done(tool_call_id: str, output: dict):
    status = "completed" if output.get("returncode", -1) == 0 else "failed"
    body = output.get("output", "") or output.get("exception_info", "") or "(no output)"
    return update_tool_call(tool_call_id, status=status,
                            content=[tool_content(text_block(body))])


def message_chunk(text: str):
    return update_agent_message_text(text)


def user_message_chunk(text: str):
    return update_user_message_text(text)


def with_meta(update, harness_meta: dict[str, Any]):
    existing = update.field_meta or {}
    update.field_meta = {**existing, "harness": harness_meta}
    return update


def parse_plan_command(command: str) -> list[tuple[str, str]] | None:
    """If `command` is the sentinel `plan ...` command, parse its args into
    (label, status) pairs; otherwise return None (so the caller runs it as a
    normal shell command). Each arg is `label:status`; status defaults to
    'pending' and unknown statuses fall back to 'pending'. Labels may contain
    colons (we split on the LAST colon). Malformed quoting returns None.

    A `plan` line that chains a real shell command (`plan "..." && gh ...`, a
    pipe, a heredoc) is NOT a pure plan — returning None runs the whole line as
    shell instead of shredding the chained command's words into checklist rows.
    Unquoted shell-control operators survive shlex.split as bare tokens; an
    operator inside a quoted label stays part of that label and is unaffected.

    Pure: no I/O. `plan` with no args is a valid empty plan -> []."""
    text = command.strip()
    if text.startswith("$ "):
        text = text[2:].strip()
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None                      # unbalanced quotes — not a plan command
    if not tokens or tokens[0] != "plan":
        return None
    if any(_is_shell_chain_token(t) for t in tokens[1:]):
        return None                      # chained real command — not a pure plan
    entries: list[tuple[str, str]] = []
    for arg in tokens[1:]:
        label, sep, status = arg.rpartition(":")
        if not sep:                      # no colon — whole arg is the label
            label, status = arg, "pending"
        if status not in _PLAN_STATUSES:
            status = "pending"
        if label:
            entries.append((label, status))
    return entries


def plan_update(entries: list[tuple[str, str]]):
    """Build an ACP plan update from (label, status) pairs."""
    return update_plan([plan_entry(label, status=status) for label, status in entries])


def trace_event(type: str, **data) -> dict:
    """Relay payload for the --debug trace: the TUI unpacks
    field_meta['harness']['trace'] and writes it with source='agent'."""
    return {"type": type, "data": data}
