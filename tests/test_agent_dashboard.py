import asyncio, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

from harness.tui.app import HarnessTui
from harness.tui.screens.agent_dashboard import AgentDashboard
from harness.tui.widgets.prompt_area import PromptArea


def test_j_opens_agent_dashboard():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.action_open_agent_dashboard()   # direct action (rail focus not required)
            await pilot.pause()
            assert isinstance(app.screen, AgentDashboard), \
                f"J did not open the dashboard: {type(app.screen).__name__}"
    asyncio.run(go())


def test_j_keypress_opens_dashboard():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # move focus off the composer/input so a printable key isn't consumed
            app.set_focus(None)
            await pilot.press("j")
            await pilot.pause()
            assert isinstance(app.screen, AgentDashboard), \
                "pressing 'j' (focus off input) did not open the dashboard"
    asyncio.run(go())


def test_j_in_focused_composer_types_not_opens():
    """A plain-letter 'j' binding must NOT hijack typing: with the composer
    focused, pressing 'j' inserts 'j' into the input and does NOT open the
    dashboard (the focused input consumes the key first). Guards the footgun of
    an app-level single-letter binding stealing keystrokes."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()
            assert not isinstance(app.screen, AgentDashboard), \
                "'j' while composer focused wrongly opened the dashboard (hijacked typing)"
            assert app.query_one("#landing-input", PromptArea).value == "j", \
                "'j' was not typed into the focused composer"
    asyncio.run(go())
