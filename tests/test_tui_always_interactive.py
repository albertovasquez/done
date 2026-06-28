import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

def _assert_interactive(app):
    inp = app._active_input()
    assert not inp.disabled, "composer disabled"
    return inp

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


def test_composer_interactive_in_every_phase():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look"
            await pilot.press("enter")

            # phase A: pre-stream gap (turn active, no chunk) — click + type
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and app._streaming_md is None:
                    break
            inp = _assert_interactive(app)
            app.set_focus(None); await pilot.pause()
            await pilot.click("#conversation-input"); await pilot.pause()
            assert app.focused is inp, "click did not focus composer in pre-stream gap"
            before = inp.value
            await pilot.press("a"); await pilot.pause()
            assert inp.value == before + "a", "keystroke lost in pre-stream gap"

            # phase B: cancel is always reachable
            await pilot.press("escape"); await pilot.pause()
            for _ in range(50):
                await pilot.pause()
                if not app._turn_active:
                    break

        # phase C: mid-burst render — separate run, MANYCHUNKS
        app2 = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app2.run_test() as pilot:
            await pilot.pause()
            app2.query_one("#landing-input", PromptArea).focus()
            app2.query_one("#landing-input", PromptArea).value = "MANYCHUNKS"
            await pilot.press("enter")
            probed = False
            for _ in range(200):
                await pilot.pause()
                if app2._streaming_md is not None and app2._turn_active:
                    inp2 = _assert_interactive(app2)
                    inp2.focus(); await pilot.pause()
                    before2 = inp2.value
                    await pilot.press("b"); await pilot.pause()
                    assert inp2.value == before2 + "b", "keystroke lost mid-burst render"
                    probed = True
                    break
            assert probed, "never caught the mid-burst window"
    asyncio.run(go())
