import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.state import AgentSnapshot, AgentState, TaskItem
from harness.tui.widgets.activity_region import ActivityRegion


def _snap(state, plan=()):
    return AgentSnapshot(id="default", name="agent", state=state, plan=plan)


def test_show_plan_true_when_working_and_plan_present():
    snap = _snap(AgentState.RUNNING_TOOL, plan=(TaskItem(label="A", status="in_progress"),))
    assert ActivityRegion.show_plan(snap) is True


def test_show_plan_false_when_no_plan():
    snap = _snap(AgentState.RUNNING_TOOL, plan=())
    assert ActivityRegion.show_plan(snap) is False


def test_show_plan_false_when_idle_even_with_plan():
    snap = _snap(AgentState.DONE, plan=(TaskItem(label="A", status="done"),))
    assert ActivityRegion.show_plan(snap) is False
