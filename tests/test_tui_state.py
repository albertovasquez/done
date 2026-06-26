import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.state import (
    AgentState, ToolStatus, ToolView, TaskItem, ScheduleView, DecisionView,
    infer_subtype,
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


def test_infer_subtype():
    assert infer_subtype("pytest tests/ -q") == "test"
    assert infer_subtype("python -m pytest x") == "test"
    assert infer_subtype("sed -i 's/a/b/' f.py") == "edit"
    assert infer_subtype("apply_patch <<EOF") == "edit"
    assert infer_subtype("cat README.md") == "read"
    assert infer_subtype("grep -r foo .") == "search"
    assert infer_subtype("rg foo") == "search"
    assert infer_subtype("echo hello") == "shell"
    assert infer_subtype("") == "shell"
    assert infer_subtype("$ pytest") == "test"   # leading "$ " stripped
