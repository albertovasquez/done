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
from harness.tui.widgets.confirm_modal import ConfirmModal
from harness.tui.widgets.prompt_area import PromptArea
from harness.jobs import model as m, ops

REPO = Path(__file__).resolve().parent.parent.parent
FAKE_CMD = [__import__("sys").executable, str(REPO / "tests/fake_agent.py")]


def _seed_job(name: str = "keep-me", job_id: str = "j1") -> m.Job:
    """Add one job to the (tmp-isolated) store and return it."""
    job = m.Job(
        id=job_id, name=name, agent_id="fred",
        schedule=m.Every(seconds=120),
        payload=m.Reminder(text="hi"),
        grant=m.Grant(tools="inherit", paths="workspace",
                      write=False, exec=False, network=False),
        cost=m.CostGate(timeout_s=10, min_cadence_s=60, max_consecutive_failures=2),
        state=m.JobState(),
    )
    ops.add(job, now=1_700_000_000.0)
    return job


def _confirm_open(app) -> bool:
    """A ConfirmModal is a separate screen root, so app.query() (current-screen
    only) can't see it — check the screen stack instead."""
    return any(isinstance(s, ConfirmModal) for s in app.screen_stack)


async def _open_cron_focused(app, pilot):
    """Open the cron drawer (real ctrl+j path — makes it visible + focuses the
    dashboard) and highlight the first job row so key bindings reach it."""
    await pilot.press("ctrl+j")
    await pilot.pause()
    dash = app.query_one("#cron-dashboard", CronDashboard)
    # index 0 is the non-selectable daemon-status header; the job is index 1.
    dash.index = 1
    await pilot.pause()
    return dash


@pytest.fixture(autouse=True)
def _no_real_daemon_spawn(monkeypatch):
    # on_mount calls _decide_cron_autostart. Default all tests to the fallback-spawn
    # path by reporting "unsupported" backend (so service checks are skipped) and
    # stubbing the detached spawn so no real subprocess is forked.
    import harness.jobs.service as svc
    monkeypatch.setattr(svc, "current_backend", lambda: "unsupported")
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


def test_on_mount_falls_back_to_spawn_when_service_absent(monkeypatch):
    """With no OS service installed and the prompt already answered, boot falls
    back to the best-effort detached spawn."""
    import harness.jobs.service as svc
    import harness.jobs.prompt_state as ps
    import harness.jobs.supervisor as sup
    from harness.jobs.service import ServiceResult
    monkeypatch.setattr(svc, "current_backend", lambda: "launchd")
    monkeypatch.setattr(svc, "service_status",
                        lambda: ServiceResult(True, "launchd", "not-installed", ""))
    monkeypatch.setattr(ps, "has_been_asked", lambda: True)
    called = []
    monkeypatch.setattr(sup, "ensure_daemon_running",
                        lambda **k: called.append(True) or "spawned")

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert called == [True]    # fallback spawn attempted exactly once

    asyncio.run(go())


def test_on_mount_survives_autostart_failure(monkeypatch):
    """A raising ensure_daemon_running must not break boot.

    Force the fallback branch (service not installed, prompt already answered)
    so the spawn is actually reached — then confirm the exception is swallowed
    and the app stays functional.
    """
    import harness.jobs.service as svc
    import harness.jobs.prompt_state as ps
    import harness.jobs.supervisor as sup
    from harness.jobs.service import ServiceResult
    monkeypatch.setattr(svc, "current_backend", lambda: "launchd")
    monkeypatch.setattr(svc, "service_status",
                        lambda: ServiceResult(True, "launchd", "not-installed", ""))
    monkeypatch.setattr(ps, "has_been_asked", lambda: True)

    def boom(**k):
        raise RuntimeError("spawn exploded")
    monkeypatch.setattr(sup, "ensure_daemon_running", boom)

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#cron-drawer") is not None   # booted anyway

    asyncio.run(go())


# ── #178: delete is confirmed, off the reflex key ────────────────────────────

def test_backspace_does_not_delete_focused_job():
    """Regression for #178: backspace must NOT delete. A reflex backspace on the
    focused roster once destroyed a live job with no prompt; the binding is gone,
    so the job survives and no confirm modal appears."""
    async def go():
        _seed_job(name="survivor")
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_cron_focused(app, pilot)
            await pilot.pause()

            await pilot.press("backspace")
            await pilot.pause()

            # job still there, and no confirm modal was pushed
            assert len(ops.list_jobs()) == 1, "backspace must not delete the job"
            assert not _confirm_open(app), "backspace must not open a confirm modal"

    asyncio.run(go())


def test_d_opens_confirm_and_does_not_delete_until_confirmed():
    """Pressing 'd' asks for confirmation and does NOT remove on its own — the
    job is still in the store while the modal is up."""
    async def go():
        _seed_job(name="pending")
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_cron_focused(app, pilot)
            await pilot.pause()

            await pilot.press("d")
            await pilot.pause()

            assert _confirm_open(app), "'d' must push a confirm modal"
            assert len(ops.list_jobs()) == 1, "job must survive until the user confirms"

    asyncio.run(go())


def test_confirm_removes_cancel_keeps():
    """esc on the confirm modal keeps the job; a fresh 'd' then 'y' removes it."""
    async def go():
        _seed_job(name="doomed")
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_cron_focused(app, pilot)
            await pilot.pause()

            # cancel path: esc dismisses without deleting
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert len(ops.list_jobs()) == 1, "cancel must keep the job"

            # confirm path: 'd' then 'y' removes it
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert len(ops.list_jobs()) == 0, "confirming must remove the job"

    asyncio.run(go())
