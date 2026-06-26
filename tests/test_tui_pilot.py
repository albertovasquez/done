import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from pathlib import Path

from harness.tui.app import HarnessTui, PermissionModal
from textual.widgets import RichLog, Input

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [str(REPO / ".venv/bin/python"), str(REPO / "tests/fake_agent.py")]


def _transcript_text(app) -> str:
    from textual.widgets import RichLog
    log = app.query_one("#transcript", RichLog)
    return "\n".join(strip.text for strip in log.lines)


def test_pilot_renders_harness_chip_end_to_end():
    """Boot app against the fake agent, type a prompt, assert the harness chip and
    the agent message both land in the transcript."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()                      # let on_mount finish (spawn+init+session)
            await pilot.click("#prompt")             # focus the Input before typing
            app.query_one("#prompt", Input).value = "hello"
            await pilot.press("enter")
            # wait for the worker turn + posted updates to render
            for _ in range(50):
                await pilot.pause()
                if "classified: chat_question" in _transcript_text(app):
                    break
            text = _transcript_text(app)
        assert "classified: chat_question" in text, f"chip missing.\n{text}"
        assert "agent:" in text and "done" in text, f"agent message missing.\n{text}"

    asyncio.run(go())


def test_pilot_permission_modal_reject():
    """Optional Smoke 2: fake agent requests permission; rejecting resolves the
    Future and the turn completes. If this proves flaky, it may be removed in
    review — the render smoke above is the required one."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#prompt")             # focus the Input before typing
            app.query_one("#prompt", Input).value = "please PERMISSION now"
            await pilot.press("enter")
            # wait for the modal to appear, then reject
            modal_seen = False
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, PermissionModal):
                    modal_seen = True
                    # press the Reject button
                    await pilot.click("#opt-__reject__")
                    break
            # let the turn finish
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            text = _transcript_text(app)
        assert modal_seen, "permission modal never appeared"
        assert "done" in text, f"turn did not complete after reject.\n{text}"

    asyncio.run(go())
