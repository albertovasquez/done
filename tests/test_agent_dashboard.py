import asyncio, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

import pytest
from textual.widgets import Input

from harness.jobs import model as m
from harness.jobs import ops
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


def _grant() -> m.Grant:
    return m.Grant(tools="*", paths="*", write=False, exec=False, network=False)


def _cost() -> m.CostGate:
    return m.CostGate(timeout_s=60, min_cadence_s=0, max_consecutive_failures=3)


def _job(*, name="Nightly sync", agent_id="default", description="Syncs data",
          enabled=True) -> m.Job:
    return m.Job(
        id=name.lower().replace(" ", "-"),
        name=name,
        agent_id=agent_id,
        schedule=m.Every(seconds=3600),
        payload=m.AgentTurn(message="go"),
        grant=_grant(),
        cost=_cost(),
        state=m.JobState(),
        description=description,
        enabled=enabled,
    )


def test_command_disables_job(tmp_path, monkeypatch):
    """Seed one enabled job for the default agent, open the dashboard, submit
    'disable <name>' through the command input, and assert the job is disabled
    in the real store (no agent session involved)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ops.add(_job(name="Nightly sync", agent_id="default"), now=1000.0)

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.action_open_agent_dashboard()
            await pilot.pause()
            assert isinstance(app.screen, AgentDashboard)

            cmd_input = app.screen.query_one("#dashboard-command", Input)
            cmd_input.focus()
            cmd_input.value = "disable Nightly sync"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(go())

    jobs = ops.list_jobs(agent_id="default")
    assert jobs[0].enabled is False
