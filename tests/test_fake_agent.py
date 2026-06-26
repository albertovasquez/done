import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from pathlib import Path
from typing import Any

import acp

REPO = Path(__file__).resolve().parent.parent
# Use the running interpreter (portable across worktrees / any cwd), not a
# hardcoded REPO/.venv path which doesn't exist in a git worktree.
CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


class _Collector:
    def __init__(self): self.updates = []
    async def session_update(self, session_id, update, **kw): self.updates.append(update)
    async def request_permission(self, options, session_id, tool_call, **kw):
        from acp.schema import RequestPermissionResponse, DeniedOutcome
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
    async def read_text_file(self, *a, **k): return None
    async def write_text_file(self, *a, **k): return None
    async def create_terminal(self, *a, **k): return None
    async def terminal_output(self, *a, **k): return None
    async def wait_for_terminal_exit(self, *a, **k): return None
    async def release_terminal(self, *a, **k): return None
    async def kill_terminal(self, *a, **k): return None
    async def ext_method(self, m, p): return {}
    async def ext_notification(self, m, p): return None
    def on_connect(self, conn): pass


def test_fake_agent_emits_harness_chip_meta():
    async def go():
        c = _Collector()
        async with acp.spawn_agent_process(c, CMD[0], *CMD[1:]) as (conn, _proc):
            await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
            new = await conn.new_session(cwd=str(REPO), mcp_servers=[])
            resp = await conn.prompt(prompt=[acp.text_block("hello")], session_id=new.session_id)
        # at least one update carries harness.task_classified
        metas = [u.field_meta for u in c.updates if getattr(u, "field_meta", None)]
        types = [m.get("harness", {}).get("task_classified", {}).get("task_type") for m in metas]
        assert "chat_question" in types, f"got {types!r}"
        assert resp.stop_reason == "end_turn"
    asyncio.run(go())
