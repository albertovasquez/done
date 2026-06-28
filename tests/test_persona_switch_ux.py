import asyncio
from pathlib import Path

from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea
from textual.containers import VerticalScroll


REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [__import__("sys").executable, str(REPO / "tests/fake_agent.py")]


def _transcript_children(app):
    try:
        return list(app.query_one("#transcript", VerticalScroll).children)
    except Exception:
        return None


async def _send_first_prompt(pilot, app, text):
    app.query_one("#landing-input", PromptArea).focus()
    app.query_one("#landing-input", PromptArea).value = text
    await pilot.press("enter")


def test_clear_transcript_empties_children_and_resets_stream_state():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            assert _transcript_children(app), "precondition: transcript has children"
            snap_before = app._snapshot          # must be preserved
            app._clear_transcript()
            await pilot.pause()
            assert _transcript_children(app) == [], "transcript not emptied"
            assert app._streaming_md is None
            assert app._stream_buf == ""
            assert app._stream_closed is True
            assert app._boundary_after is False
            assert app._snapshot is snap_before, "_clear_transcript must NOT touch _snapshot"

    asyncio.run(go())
