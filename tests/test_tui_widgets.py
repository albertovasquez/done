import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from textual.app import App, ComposeResult

from harness.tui.state import AgentState
from harness.tui.widgets.status_chip import (
    StatusChip, StateDot, ActivityGlyph, state_color_token,
)


def test_state_color_token_mapping():
    assert state_color_token(AgentState.RUNNING_TOOL) == "accent"
    assert state_color_token(AgentState.DONE) == "success"
    assert state_color_token(AgentState.SCHEDULED) == "scheduled"
    assert state_color_token(AgentState.FAILED) == "error"
    assert state_color_token(AgentState.IDLE) == "muted"


def test_status_chip_renders_uppercase_label():
    chip = StatusChip.from_state(AgentState.RUNNING_TOOL)
    # the rendered markup contains the uppercase chip label
    assert "RUNNING" in chip._label


def test_activity_glyph_reduced_motion_is_static():
    g = ActivityGlyph(reduced_motion=True)
    assert g._frames_static is True
