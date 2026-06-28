#!/usr/bin/env python3
"""A minimal ACP agent for TUI tests. No real model. Emits one agent message
carrying a field_meta["harness"]["task_classified"] chip; if the prompt text
contains "PERMISSION", it requests permission once (so the modal flow can be
driven). STDOUT is the JSON-RPC wire — never print to stdout."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "upstream" / "src"))
sys.path.insert(0, str(REPO))

import acp
from acp import update_agent_message_text
from acp.schema import AgentCapabilities, PermissionOption, ToolCallUpdate


class FakeAgent(acp.Agent):
    def __init__(self):
        self._conn = None
        self._sessions = set()

    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(load_session=False),
        )

    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw):
        sid = "fake-session"
        self._sessions.add(sid)
        return acp.NewSessionResponse(session_id=sid)

    async def prompt(self, prompt, session_id, message_id=None, **kw):
        text = "".join(getattr(b, "text", "") for b in prompt)

        # 1) emit a harness chip via field_meta (the differentiator under test)
        upd = update_agent_message_text("")
        upd.field_meta = {"harness": {"task_classified": {
            "task_type": "chat_question", "skills": [], "confidence": 1.0}}}
        await self._conn.session_update(session_id, upd)

        # 1b) when asked, relay a --debug trace payload so the client's
        # extract_agent_trace path can be exercised end-to-end over a real
        # subprocess (the production agent does this via with_meta/RelayEmitter).
        if "TRACE" in text:
            tupd = update_agent_message_text("")
            tupd.field_meta = {"harness": {"trace": {
                "type": "llm.call", "data": {"sid": session_id, "n": 1}}}}
            await self._conn.session_update(session_id, tupd)

        # 2) optionally drive a permission round-trip
        if "PERMISSION" in text:
            options = [
                PermissionOption(kind="allow_once", name="Allow once", option_id="allow_once"),
                PermissionOption(kind="reject_once", name="Reject", option_id="reject_once"),
            ]
            await self._conn.request_permission(
                options=options, session_id=session_id,
                # title carries the real command (like the production agent), so
                # the client shows "$ <cmd>" not the opaque tool_call_id.
                tool_call=ToolCallUpdate(tool_call_id="tc1", title="$ echo hello"))

        # 2c) BURST: faithfully replay the real "Responding…"-stuck turn shape —
        # a chat_question whose tiny answer + chat.done relay + response frame all
        # land in one ~8ms burst (real trace 20260628-131123). Several small
        # AgentMessageChunks, an agent-relayed chat.done trace, then return — NO
        # awaits between them, so the whole sequence delivers in a single tick.
        # Used by the stress Pilot loop to hunt the TurnEnded-never-fires race.
        if "BURST" in text:
            for piece in ("I have ", "**7 skills** available:\n", "- ask-done\n",
                          "- clarify-before-acting\n", "- planning-before-coding\n"):
                await self._conn.session_update(
                    session_id, update_agent_message_text(piece))
            tupd = update_agent_message_text("")
            tupd.field_meta = {"harness": {"trace": {
                "type": "chat.done", "data": {"sid": session_id}}}}
            await self._conn.session_update(session_id, tupd)
            return acp.PromptResponse(stop_reason="end_turn")

        # 2d) SLOW: a long pre-stream gap with NOTHING emitted — faithfully
        # replays the agent-path "spinner, nothing printing yet" window (the
        # ~4.5s uncached router classify before the first frame reaches the TUI,
        # real trace 20260628-151948 turn 2). The chip in step (1) already went
        # out; here we just stall, then emit one delta and return. Used by the
        # input-freeze repro to probe composer usability DURING that gap.
        if "SLOW" in text:
            await asyncio.sleep(0.6)
            await self._conn.session_update(
                session_id, update_agent_message_text("late answer"))
            return acp.PromptResponse(stop_reason="end_turn")

        # MANYCHUNKS: emit many small deltas spread across real wall-clock time
        # so that the invariant test (test_tui_always_interactive.py Phase C)
        # can observe the mid-burst window (turn_active AND streaming_md set).
        # Without real sleeps the TUI processes all 60 chunks before
        # pilot.pause()'s wait_for_idle(0) returns, collapsing the window.
        # Inter-chunk sleep of 30ms spreads 60 chunks over ~1.8s; the 12Hz
        # coalescing timer fires ~21 times in that window, so render count
        # stays well below 60 (coalescing guarantee intact, threshold raised).
        if "MANYCHUNKS" in text:
            for i in range(60):
                await self._conn.session_update(
                    session_id, update_agent_message_text(f"word{i} "))
                await asyncio.sleep(0.03)   # 30ms: ~21 timer fires for 60 chunks
            return acp.PromptResponse(stop_reason="end_turn")

        # 3) optionally stream several message deltas for ONE turn (so the client
        # can be tested accumulating them into a single live Markdown widget).
        if "STREAM" in text:
            # a small pre-token delay so the client's "working" indicator is
            # observable (a real turn has latency before the first token).
            await asyncio.sleep(0.15)
            for piece in ("Hello ", "**world** ", "done"):
                await self._conn.session_update(
                    session_id, update_agent_message_text(piece))
            return acp.PromptResponse(stop_reason="end_turn")

        # 4) a normal agent message
        await self._conn.session_update(session_id, update_agent_message_text("done"))
        return acp.PromptResponse(stop_reason="end_turn")


async def _main():
    import os
    marker = os.getenv("FAKE_AGENT_STARTS_FILE")
    if marker:
        with open(marker, "a") as f:
            f.write("start\n")
    await acp.run_agent(FakeAgent())


if __name__ == "__main__":
    asyncio.run(_main())
