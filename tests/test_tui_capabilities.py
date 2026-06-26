import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
import shutil
from pathlib import Path

import pytest
import acp
from acp.schema import ClientCapabilities, ElicitationCapabilities, RequestPermissionResponse, AllowedOutcome

REPO = Path(__file__).resolve().parent.parent
AGENT_CMD = [str(REPO / ".venv/bin/python"), str(REPO / "harness/acp_main.py"), "--model", "mock"]
SAMPLE = REPO / "examples" / "sample-repo"


def _vibeproxy_up() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8317/v1/models", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


needs_vibeproxy = pytest.mark.skipif(not _vibeproxy_up(),
    reason="VibeProxy not reachable at localhost:8317 — classification test skipped")


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


@needs_vibeproxy
def test_no_fs_or_terminal_calls_under_elicitation_only(tmp_path):
    repo = tmp_path / "sample-repo"
    shutil.copytree(SAMPLE, repo)
    target = repo / "calculator.py"
    assert "return a - b" in target.read_text(), "fixture sanity"

    async def go():
        client = _StrictClient()
        async with acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:]) as (conn, _proc):
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
