"""Visual-snapshot regression tests for the Done TUI.

First target: completed-turn ORDERING (prompt -> answer -> footer). This is the
#138 / #81 / #97 / #100 bug class, invisible to state-only Pilot tests."""
from __future__ import annotations

from tests.tui_snapshot_harness import (
    FAKE_CMD,
    REPO,
    isolated_default_persona,   # noqa: F401  (used as an autouse fixture below)
    drive_completed_turn,
)

import pytest
from harness.tui.app import HarnessTui


@pytest.fixture(autouse=True)
def _iso(isolated_default_persona):   # activate XDG isolation for every test here
    yield


def test_completed_turn_ordering(snap_compare):
    """One full turn, captured after it settles. Locks prompt->answer->footer
    order and spacing."""
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")

    async def run_before(pilot):
        await drive_completed_turn(pilot, app, "hello there")

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))
