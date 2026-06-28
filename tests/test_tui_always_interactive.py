import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

def test_placeholder_shows_queue_hint_during_turn():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look at file"
            await pilot.press("enter")
            seen = False
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and "queue" in app._active_input().placeholder.lower():
                    seen = True
                    break
            assert seen, f"placeholder never showed queue hint (was {app._active_input().placeholder!r})"
            for _ in range(50):
                await pilot.pause()
                if not app._turn_active:
                    break
            assert "queue" not in app._active_input().placeholder.lower(), \
                "placeholder stuck on queue hint after turn ended"
    asyncio.run(go())
