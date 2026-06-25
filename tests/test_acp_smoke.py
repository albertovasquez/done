"""Smoke/integration tests: launch the harness agent as a real subprocess and
drive it over the ACP JSON-RPC protocol.

Connection pattern (verified against SDK v0.10.1):
    async with acp.spawn_agent_process(client, cmd, *args) as (conn, proc):
        init = await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
        ...

`spawn_agent_process` is an @asynccontextmanager that yields (ClientSideConnection, Process).
`acp.Client` is a Protocol; implement every method concretely.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import acp
from acp.schema import (
    ClientCapabilities,
    DeniedOutcome,
    ElicitationCapabilities,
    RequestPermissionResponse,
)

REPO = Path(__file__).resolve().parent.parent
AGENT_CMD = [
    str(REPO / ".venv/bin/python"),
    str(REPO / "trace/acp_main.py"),
    "--model", "mock",
]

# ---------------------------------------------------------------------------
# VibeProxy reachability guard
# ---------------------------------------------------------------------------

def _vibeproxy_up() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8317/v1/models", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


VIBEPROXY_UP = _vibeproxy_up()
needs_vibeproxy = pytest.mark.skipif(
    not VIBEPROXY_UP,
    reason="VibeProxy not reachable at localhost:8317 — classification tests skipped",
)

# ---------------------------------------------------------------------------
# Collecting client — implements the full acp.Client Protocol
# ---------------------------------------------------------------------------

class _CollectingClient:
    """Concrete Client implementation that records all session_update calls."""

    def __init__(self):
        self.updates: list[Any] = []

    # Required by the Protocol
    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)

    async def request_permission(self, options: Any, session_id: str, tool_call: Any, **kwargs: Any) -> Any:
        # Layer 1 never calls this; raise to surface unexpected calls
        raise NotImplementedError("request_permission called unexpectedly in Layer-1 test")

    async def write_text_file(self, content: str, path: str, session_id: str, **kwargs: Any) -> Any:
        return None

    async def read_text_file(self, path: str, session_id: str, **kwargs: Any) -> Any:
        return None

    async def create_terminal(self, command: str, session_id: str, **kwargs: Any) -> Any:
        return None

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        return None

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        return None

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        return None

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        return None

    async def ext_method(self, method: str, params: dict) -> dict:
        return {}

    async def ext_notification(self, method: str, params: dict) -> None:
        pass

    def on_connect(self, conn: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Rejecting client — rejects every permission request
# ---------------------------------------------------------------------------

class _RejectingClient(_CollectingClient):
    """Like _CollectingClient but rejects all permission requests."""

    async def request_permission(self, options: Any, session_id: str, tool_call: Any, **kwargs: Any) -> Any:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


class _TerminalRecordingClient(_CollectingClient):
    """Records create_terminal calls and executes commands locally (simulates client terminal)."""

    def __init__(self):
        super().__init__()
        self.terminal_calls: list[str] = []  # commands passed to create_terminal
        self._terminals: dict[str, Any] = {}  # terminal_id -> subprocess result
        self._next_id = 0

    async def create_terminal(self, command: str, session_id: str, **kwargs: Any) -> Any:
        import subprocess
        from acp.schema import CreateTerminalResponse
        self.terminal_calls.append(command)
        self._next_id += 1
        tid = f"term-{self._next_id}"
        # Run the command synchronously (test context, no concurrency concerns)
        proc = subprocess.run(command, shell=True, text=True, capture_output=True)
        self._terminals[tid] = proc
        return CreateTerminalResponse(terminal_id=tid)

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        from acp.schema import WaitForTerminalExitResponse
        proc = self._terminals.get(terminal_id)
        exit_code = proc.returncode if proc is not None else 0
        return WaitForTerminalExitResponse(exit_code=exit_code, signal=None)

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        from acp.schema import TerminalOutputResponse, TerminalExitStatus
        proc = self._terminals.get(terminal_id)
        output = (proc.stdout + proc.stderr) if proc is not None else ""
        exit_code = proc.returncode if proc is not None else 0
        exit_status = TerminalExitStatus(exit_code=exit_code, signal=None)
        return TerminalOutputResponse(output=output, exit_status=exit_status, truncated=False)

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        self._terminals.pop(terminal_id, None)


# ---------------------------------------------------------------------------
# Helper: spawn + drive
# ---------------------------------------------------------------------------

async def _drive(prompt_text: str, cwd: Path):
    """Initialize, open a session, send a prompt; return (updates, response)."""
    client = _CollectingClient()
    async with acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:]) as (conn, _proc):
        await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
        new = await conn.new_session(cwd=str(cwd), mcp_servers=[])
        resp = await conn.prompt(
            prompt=[acp.text_block(prompt_text)], session_id=new.session_id
        )
        return client.updates, resp


# ---------------------------------------------------------------------------
# Test A: initialize → new_session returns session_id and correct protocol_version
# ---------------------------------------------------------------------------

def test_initialize_and_new_session(tmp_path):
    """The handshake must succeed and echo back PROTOCOL_VERSION."""
    async def go():
        client = _CollectingClient()
        async with acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:]) as (conn, _proc):
            init = await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
            assert init.protocol_version == acp.PROTOCOL_VERSION, (
                f"expected {acp.PROTOCOL_VERSION}, got {init.protocol_version}"
            )
            new = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
            assert new.session_id, "new_session must return a non-empty session_id"

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Test B: chat_question prompt → _meta task_type==chat_question, no ToolCall
# ---------------------------------------------------------------------------

@needs_vibeproxy
def test_chat_question_no_tool_call(tmp_path):
    """'what is 1+1' must classify as chat_question and NOT trigger any tool call."""
    updates, resp = asyncio.run(_drive("what is 1+1", tmp_path))

    assert resp.stop_reason == "end_turn", f"unexpected stop_reason: {resp.stop_reason}"

    # Collect all field_meta dicts from updates
    metas = [u.field_meta for u in updates if getattr(u, "field_meta", None) is not None]

    # At least one update must carry harness task_classified metadata
    classified_types = [
        m.get("harness", {}).get("task_classified", {}).get("task_type")
        for m in metas
    ]
    assert any(t == "chat_question" for t in classified_types), (
        f"expected a 'chat_question' classification in _meta, got: {classified_types!r}\n"
        f"All field_metas: {metas!r}"
    )

    # No ToolCall update should appear (Phase-2 guarantee: chat skips the agent engine)
    tool_call_types = {type(u).__name__ for u in updates if "ToolCall" in type(u).__name__}
    assert not tool_call_types, (
        f"chat_question must not produce ToolCall updates, got: {tool_call_types}"
    )


# ---------------------------------------------------------------------------
# Test C: stdout purity — every non-empty byte emitted by the subprocess on
# stdout must be valid JSON-RPC (i.e., valid JSON). A banner or log line would
# break the protocol wire.
# ---------------------------------------------------------------------------

def test_stdout_purity(tmp_path):
    """The agent subprocess must emit only valid JSON on stdout; no banners or logs.

    Primary proof: the SDK's newline-delimited JSON parser would raise on any
    non-JSON byte, so a successful initialize() proves wire purity.

    Secondary proof: launch a raw subprocess, send a hand-crafted newline-
    delimited JSON-RPC initialize request, read exactly one response line, and
    assert it parses as JSON.
    """
    # Primary: SDK-level proof
    async def go():
        client = _CollectingClient()
        async with acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:]) as (conn, _proc):
            init = await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
            assert init.protocol_version == acp.PROTOCOL_VERSION
        return True

    assert asyncio.run(go()), "SDK-level handshake failed"

    # Secondary: raw line-by-line proof via a dedicated async reader
    # Send a hand-crafted newline-delimited JSON-RPC initialize request and
    # read the first response line — every byte the agent writes to stdout
    # must parse as JSON.
    async def raw_check():
        import asyncio.subprocess as aio_sp
        proc = await asyncio.create_subprocess_exec(
            *AGENT_CMD,
            stdin=aio_sp.PIPE,
            stdout=aio_sp.PIPE,
            stderr=aio_sp.DEVNULL,
        )
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": acp.PROTOCOL_VERSION},
        }).encode() + b"\n"
        try:
            proc.stdin.write(req)
            await proc.stdin.drain()
            # Read one response line with a generous timeout
            first_line = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
        finally:
            proc.kill()
            await proc.wait()
        return first_line.strip()

    first_line = asyncio.run(raw_check())
    assert first_line, "agent produced no output on stdout after initialize request"
    try:
        json.loads(first_line)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"Non-JSON line on agent stdout (wire pollution): {first_line!r}\nError: {exc}"
        )


# ---------------------------------------------------------------------------
# Test D: permission reject — a client that rejects every permission request
# must prevent the command from running; file must be unchanged.
# ---------------------------------------------------------------------------

_SAMPLE_REPO = Path(__file__).resolve().parent.parent / "examples" / "sample-repo"


@needs_vibeproxy
def test_permission_reject_skips_command(tmp_path):
    """A rejecting client must prevent shell commands from running.

    Drives a code-fix prompt against a temp copy of examples/sample-repo.
    The mock model will attempt to run a sed/patch command (tool call). Because
    the client rejects every permission request, the command must be skipped
    and the file must remain unchanged (still contains 'return a - b').
    """
    # Copy sample-repo to tmp_path to avoid mutating the fixture
    repo = tmp_path / "sample-repo"
    shutil.copytree(_SAMPLE_REPO, repo)
    target = repo / "calculator.py"
    original = target.read_text()
    assert "return a - b" in original, "fixture sanity: calculator.py must have the bug"

    async def go():
        client = _RejectingClient()
        async with acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:]) as (conn, _proc):
            # advertise elicitation so the agent gates shell commands on permission
            await conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(elicitation=ElicitationCapabilities()),
            )
            new = await conn.new_session(cwd=str(repo), mcp_servers=[])
            resp = await conn.prompt(
                prompt=[acp.text_block(
                    "Fix the bug in calculator.py: the add function returns a - b, "
                    "it should return a + b."
                )],
                session_id=new.session_id,
            )
            return client.updates, resp

    updates, resp = asyncio.run(go())

    # The file must NOT have been modified (permission was rejected)
    assert target.read_text() == original, (
        "calculator.py was modified despite permission rejection"
    )

    # A ToolCallStart must have appeared (agent tried to run a command)
    tool_call_starts = [u for u in updates if type(u).__name__ == "ToolCallStart"]
    assert tool_call_starts, (
        "expected at least one ToolCallStart update, got none — "
        "agent may not have attempted a command"
    )

    # At least one ToolCallProgress with status 'failed' must appear (rejected command)
    # (acp.update_tool_call returns ToolCallProgress, not ToolCallUpdate)
    tool_call_progresses = [u for u in updates if type(u).__name__ == "ToolCallProgress"]
    failed_statuses = [
        getattr(u, "status", None)
        for u in tool_call_progresses
        if getattr(u, "status", None) is not None
    ]
    assert any(
        str(s) in ("failed", "ToolCallStatus.failed") for s in failed_statuses
    ), (
        f"expected a 'failed' ToolCallProgress after rejection, got statuses: {failed_statuses!r}\n"
        f"All update types: {[type(u).__name__ for u in updates]!r}"
    )


# ---------------------------------------------------------------------------
# Test E: terminal delegation — client advertises terminal capability;
# agent must delegate shell commands via client terminal/* methods.
# ---------------------------------------------------------------------------

@needs_vibeproxy
def test_terminal_delegation_uses_client_terminal(tmp_path):
    """When the client advertises terminal=True, commands must run via create_terminal.

    Drives a code-fix prompt; asserts create_terminal was called (delegation
    happened). Uses a tmp copy of examples/sample-repo.
    """
    repo = tmp_path / "sample-repo"
    shutil.copytree(_SAMPLE_REPO, repo)
    assert "return a - b" in (repo / "calculator.py").read_text(), "fixture sanity"

    async def go():
        client = _TerminalRecordingClient()
        async with acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:]) as (conn, _proc):
            await conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(terminal=True),
            )
            new = await conn.new_session(cwd=str(repo), mcp_servers=[])
            await conn.prompt(
                prompt=[acp.text_block(
                    "Fix the bug in calculator.py: the add function returns a - b, "
                    "it should return a + b."
                )],
                session_id=new.session_id,
            )
        return client

    client = asyncio.run(go())

    assert client.terminal_calls, (
        "expected create_terminal to be called at least once (terminal delegation), "
        f"but terminal_calls is empty.\nUpdates: {[type(u).__name__ for u in client.updates]!r}"
    )


# ---------------------------------------------------------------------------
# Test F: terminal fallback — client WITHOUT terminal capability must run
# commands via LocalEnvironment (file actually changes).
# ---------------------------------------------------------------------------

@needs_vibeproxy
def test_terminal_fallback_uses_local_environment(tmp_path):
    """When the client does NOT advertise terminal capability, LocalEnvironment runs the command.

    The bug fix must actually be applied (file changes on disk).
    Uses a tmp copy of examples/sample-repo — never mutates the real fixture.
    """
    repo = tmp_path / "sample-repo"
    shutil.copytree(_SAMPLE_REPO, repo)
    target = repo / "calculator.py"
    assert "return a - b" in target.read_text(), "fixture sanity"

    async def go():
        client = _CollectingClient()
        async with acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:]) as (conn, _proc):
            # No terminal capability advertised → LocalEnvironment fallback
            await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
            new = await conn.new_session(cwd=str(repo), mcp_servers=[])
            await conn.prompt(
                prompt=[acp.text_block(
                    "Fix the bug in calculator.py: the add function returns a - b, "
                    "it should return a + b."
                )],
                session_id=new.session_id,
            )
        return client

    asyncio.run(go())

    # File should have been modified via LocalEnvironment (returncode from real shell)
    assert "return a + b" in target.read_text(), (
        "calculator.py was NOT fixed — LocalEnvironment fallback did not execute the command"
    )
