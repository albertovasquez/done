"""Integration tests for the cron drawer mounted into HarnessTui.

Mirrors tests/test_persona_switch_ux.py: launch the real app in mock mode via
`run_test()` and drive it with the Pilot. The cron drawer must mirror the agents
drawer — hidden (display=False) until ctrl+j, then toggled.

The cron store is isolated to tmp_path (harness.paths.config_dir monkeypatch,
same pattern as tests/jobs/test_ops.py) so list_jobs() never touches the real
store; with no jobs it returns [], which is all the toggle path needs.
"""
import asyncio
from pathlib import Path

import pytest

from harness.tui.app import HarnessTui
from harness.tui.widgets.cron_dashboard import CronDashboard, NewJobRequested
from harness.tui.widgets.cron_detail import CronDetail
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent.parent
FAKE_CMD = [__import__("sys").executable, str(REPO / "tests/fake_agent.py")]


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    # Isolate the cron store so ops.list_jobs() reads an empty tmp store.
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def test_cron_drawer_hidden_by_default():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            drawer = app.query_one("#cron-drawer")
            assert drawer.display is False, "cron drawer must start hidden"
            # the two child widgets are mounted
            assert app.query_one("#cron-dashboard", CronDashboard) is not None
            assert app.query_one("#cron-detail", CronDetail) is not None

    asyncio.run(go())


def test_ctrl_j_toggles_cron_drawer():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            drawer = app.query_one("#cron-drawer")
            assert drawer.display is False

            await pilot.press("ctrl+j")
            await pilot.pause()
            assert drawer.display is True, "ctrl+j should open the cron drawer"

            await pilot.press("ctrl+j")
            await pilot.pause()
            assert drawer.display is False, "ctrl+j again should close it"

    asyncio.run(go())


def test_new_job_seeds_create_prompt_and_closes_drawer():
    """Pressing 'n' (NewJobRequested) seeds the create-job skill prompt to the
    agent (not a modal / direct write) and closes the drawer."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # open the drawer first
            app.action_toggle_cron()
            await pilot.pause()
            assert app.query_one("#cron-drawer").display is True

            captured = {}

            async def _fake_submit(text):
                captured["text"] = text

            app._submit_text = _fake_submit
            app._turn_active = False
            app.on_new_job_requested(NewJobRequested())
            await pilot.pause()

            assert "create" in captured.get("text", "").lower(), \
                "should seed a create-job prompt to the agent"
            assert app.query_one("#cron-drawer").display is False, \
                "drawer should close after requesting a new job"

    asyncio.run(go())
