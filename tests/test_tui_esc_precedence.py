# tests/test_tui_esc_precedence.py  (new file)
import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

def test_esc_during_turn_cancels_even_with_text_in_box():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        cancelled = []
        async with app.run_test() as pilot:
            await pilot.pause()
            # stub the connection's cancel to record the call
            orig = app.action_cancel
            async def spy():
                cancelled.append(True)
                await orig()
            app.action_cancel = spy
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look at file"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and app._streaming_md is None:
                    break
            # type into the box, then ESC — must cancel the turn, not just clear text
            app._active_input().value = "half typed next prompt"
            await pilot.press("escape")
            await pilot.pause()
            assert cancelled, "ESC during turn did not trigger action_cancel"
            assert app._active_input().value == "half typed next prompt", \
                "ESC during turn wrongly cleared the typed text (R6: first ESC cancels, text stays)"
            # wait for the in-flight worker to finish so teardown doesn't crash
            for _ in range(80):
                await pilot.pause()
                if not app._turn_active:
                    break
    asyncio.run(go())
