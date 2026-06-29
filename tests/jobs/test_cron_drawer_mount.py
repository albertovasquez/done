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
from harness.tui.widgets.cron_dashboard import CronDashboard
from harness.tui.widgets.cron_detail import CronDetail
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent.parent
FAKE_CMD = [__import__("sys").executable, str(REPO / "tests/fake_agent.py")]


@pytest.fixture(autouse=True)
def _no_real_daemon_spawn(monkeypatch):
    # on_mount now auto-starts the cron daemon. Stub the detached spawn so the
    # mount tests never fork a real `python -m harness.jobs.cron_main` subprocess.
    monkeypatch.setattr("harness.jobs.supervisor._spawn_detached", lambda: None)


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
            dash = app.query_one("#cron-dashboard", CronDashboard)
            detail = app.query_one("#cron-detail", CronDetail)
            assert dash is not None
            assert detail is not None
            # framed-panel chrome: each region carries a border-title so the drawer
            # reads as a bordered panel, not bare floating rows.
            assert dash.border_title == "CRON JOBS"
            assert detail.border_title == "Run history"

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


def test_daemon_status_header_row_mounted():
    """The roster's first row is the daemon-status header: non-selectable
    (disabled, no .data so action guards no-op) and color-rendered. With the
    isolated empty tmp store the daemon never ran → 'not running' (red)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            dash = app.query_one("#cron-dashboard", CronDashboard)
            dash.set_rows([])                 # no jobs → header only
            await pilot.pause()

            header = dash.children[0]
            assert header.disabled is True, "header must be non-selectable"
            assert getattr(header, "data", None) is None, "header carries no job id"

            static = header.children[0]
            content = static.render()             # Rich Content from [color]…[/] markup
            assert "not running" in content.plain and content.plain.startswith("✗")
            # color travels with the text as a span (red = the 'stopped' status)
            assert any("red" in str(span.style) for span in content.spans), \
                "header text must carry the status color as a markup span"

    asyncio.run(go())


def test_on_mount_calls_ensure_daemon_running(monkeypatch):
    """Boot auto-starts the cron daemon exactly once per window."""
    import harness.jobs.supervisor as sup
    called = []
    monkeypatch.setattr(sup, "ensure_daemon_running",
                        lambda *a, **k: called.append(True) or "spawned")

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert called == [True]    # autostart attempted exactly once

    asyncio.run(go())


def test_on_mount_survives_autostart_failure(monkeypatch):
    """A raising ensure_daemon_running must not break boot."""
    import harness.jobs.supervisor as sup

    def boom(*a, **k):
        raise RuntimeError("spawn exploded")
    monkeypatch.setattr(sup, "ensure_daemon_running", boom)

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#cron-drawer") is not None   # booted anyway

    asyncio.run(go())
