import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.state import (
    AgentState, ToolStatus, ToolView, TaskItem, ScheduleView, DecisionView,
)


def test_agent_state_values():
    assert AgentState.IDLE.value == "idle"
    assert AgentState.RUNNING_TOOL.value == "running_tool"
    assert AgentState.AWAITING_DECISION.value == "awaiting_decision"


def test_tool_status_values():
    assert ToolStatus.PENDING.value == "pending"
    assert ToolStatus.DONE.value == "done"


def test_value_types_are_frozen():
    tv = ToolView(title="$ ls", status=ToolStatus.ACTIVE, subtype="shell")
    assert tv.body == ""
    dv = DecisionView(question="q?", options=(("a", "because"),))
    assert dv.options[0] == ("a", "because")
    ti = TaskItem(label="do x", status="pending")
    sv = ScheduleView(label="nightly", when="in 2d")
    assert (ti.label, sv.when) == ("do x", "in 2d")
