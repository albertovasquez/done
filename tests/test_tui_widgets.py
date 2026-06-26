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


from harness.tui.state import AgentSnapshot
from harness.tui.widgets.activity_status import ActivityStatus


def test_activity_status_renders_label_elapsed_tokens():
    w = ActivityStatus()
    snap = AgentSnapshot(id="default", name="agent", state=AgentState.RESPONDING,
                         activity_label="Responding", elapsed=78.0, tokens=4000)
    text = w.line_for(snap)
    assert "Responding" in text
    assert "4.0" in text or "4000" in text     # token formatting
    assert "78" in text or "1m" in text         # elapsed formatting


def test_activity_status_blank_when_idle():
    w = ActivityStatus()
    snap = AgentSnapshot(id="default", name="agent", state=AgentState.IDLE)
    assert w.line_for(snap).strip() == ""


from harness.tui.state import TaskItem, ToolView, ToolStatus
from harness.tui.widgets.task_tree import TaskTree
from harness.tui.widgets.tool_call_row import ToolCallRow


def test_task_tree_glyphs():
    tt = TaskTree()
    lines = tt.lines_for((
        TaskItem("explore", "done"),
        TaskItem("ask", "in_progress"),
        TaskItem("plan", "pending"),
        TaskItem("boom", "failed"),
    ))
    assert "✓" in lines[0] and "explore" in lines[0]
    assert "▣" in lines[1]
    assert "□" in lines[2]
    assert "✗" in lines[3]


def test_tool_call_row_line():
    row = ToolCallRow(ToolView(title="$ pytest tests/", status=ToolStatus.ACTIVE, subtype="test"))
    line = row.line_for(row._tool)
    assert "⚑" in line                # test subtype glyph
    assert "pytest" in line
