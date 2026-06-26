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
    """The conversation transcript only exists AFTER the first send (the UI starts
    on the centered landing screen). Returns '' until then."""
    try:
        log = app.query_one("#transcript", RichLog)
    except Exception:
        return ""
    return "\n".join(strip.text for strip in log.lines)


async def _send_first_prompt(pilot, app, text: str) -> None:
    """Type into the landing compose box and submit — this transitions the app
    from the landing state to the conversation state."""
    app.query_one("#landing-input", Input).focus()
    app.query_one("#landing-input", Input).value = text
    await pilot.press("enter")


def test_pilot_starts_on_landing_then_switches_to_conversation():
    """The app boots on the centered landing screen (wordmark + compose, no
    transcript) and switches to the conversation view after the first send."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # landing state: wordmark + landing input present, no transcript yet
            assert app.query("#landing"), "landing container should exist at boot"
            assert app.query("#landing-input"), "landing input should exist at boot"
            assert not app.query("#transcript"), "transcript must NOT exist before first send"
            assert app.theme == "harness", f"harness theme not active: {app.theme}"

            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            # conversation state: landing gone, transcript + bottom composer present
            assert app._started, "did not transition to conversation state"
            assert not app.query("#landing"), "landing should be removed after first send"
            assert app.query("#conversation-input"), "conversation input should exist"

    asyncio.run(go())


def test_pilot_renders_harness_chip_end_to_end():
    """Boot, send a prompt, and assert the harness _meta chip, the agent reply,
    and the per-turn meta line all render in the transcript end-to-end."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()                      # on_mount: spawn+init+session
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "classified: chat_question" in _transcript_text(app):
                    break
            text = _transcript_text(app)
        assert "classified: chat_question" in text, f"harness chip missing.\n{text}"
        assert "hello" in text, f"user message missing.\n{text}"      # rendered as '▌ hello'
        assert "done" in text, f"agent reply missing.\n{text}"
        assert "▣ Build" in text, f"per-turn meta line missing.\n{text}"

    asyncio.run(go())


def test_pilot_permission_modal_reject():
    """Optional Smoke: fake agent requests permission; rejecting resolves the
    Future and the turn completes."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "please PERMISSION now")
            modal_seen = False
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, PermissionModal):
                    modal_seen = True
                    await pilot.click("#opt-__reject__")
                    break
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            text = _transcript_text(app)
        assert modal_seen, "permission modal never appeared"
        assert "done" in text, f"turn did not complete after reject.\n{text}"

    asyncio.run(go())
