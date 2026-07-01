import sys

import asyncio
import shutil
from pathlib import Path

import acp
from acp.schema import ClientCapabilities, ElicitationCapabilities, RequestPermissionResponse, AllowedOutcome

REPO = Path(__file__).resolve().parent.parent
# Running interpreter + module invocation = portable (worktree / any cwd).
AGENT_CMD = [sys.executable, "-m", "harness.acp_main", "--model", "mock"]
SAMPLE = REPO / "examples" / "sample-repo"


class _StrictClient:
    """Allows permission, but RAISES if any fs/terminal method is called."""
    def __init__(self): self.updates = []
    async def session_update(self, session_id, update, **kw): self.updates.append(update)
    async def request_permission(self, options, session_id, tool_call, **kw):
        # allow so the command runs via LocalEnvironment
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="allow_once"))
    async def read_text_file(self, *a, **k): raise AssertionError("read_text_file called")
    async def write_text_file(self, *a, **k): raise AssertionError("write_text_file called")
    async def create_terminal(self, *a, **k): raise AssertionError("create_terminal called")
    async def terminal_output(self, *a, **k): raise AssertionError("terminal_output called")
    async def wait_for_terminal_exit(self, *a, **k): raise AssertionError("wait_for_terminal_exit called")
    async def release_terminal(self, *a, **k): raise AssertionError("release_terminal called")
    async def kill_terminal(self, *a, **k): raise AssertionError("kill_terminal called")
    async def ext_method(self, m, p): return {}
    async def ext_notification(self, m, p): return None
    def on_connect(self, conn): pass


def test_no_fs_or_terminal_calls_under_elicitation_only(tmp_path):
    repo = tmp_path / "sample-repo"
    shutil.copytree(SAMPLE, repo)
    target = repo / "calculator.py"
    assert "return a - b" in target.read_text(), "fixture sanity"

    async def go():
        client = _StrictClient()
        # HARNESS_ROUTER_STUB=1 so the router classifies OFFLINE — the spawn does
        # not inherit this process's env, so it must be passed explicitly (the
        # mock worker, not a live proxy, performs the edit). See #229 / PR #203.
        async with acp.spawn_agent_process(
            client, AGENT_CMD[0], *AGENT_CMD[1:],
            env={"HARNESS_ROUTER_STUB": "1"},
        ) as (conn, _proc):
            await conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(elicitation=ElicitationCapabilities()),
            )
            new = await conn.new_session(cwd=str(repo), mcp_servers=[])
            await conn.prompt(prompt=[acp.text_block(
                "Fix the bug in calculator.py: the add function returns a - b, it should return a + b."
            )], session_id=new.session_id)

    # The turn must complete WITHOUT any fs/terminal stub raising.
    asyncio.run(go())
    # and the command ran via LocalEnvironment fallback (file fixed)
    assert "return a + b" in target.read_text(), "LocalEnvironment fallback did not run the command"
