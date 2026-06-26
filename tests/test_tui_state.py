import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.state import (
    AgentState, ToolStatus, ToolView, TaskItem, ScheduleView, DecisionView,
    AgentSnapshot, FleetSnapshot, initial_snapshot,
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


def test_initial_snapshot_one_idle_agent():
    fs = initial_snapshot()
    assert len(fs.agents) == 1
    a = fs.active
    assert a is not None
    assert a.id == "default"
    assert a.state == AgentState.IDLE
    assert a.elapsed == 0.0 and a.tokens == 0 and a.tasks == ()


def test_fleet_active_returns_none_when_missing():
    fs = FleetSnapshot(agents=(), active_id="nope")
    assert fs.active is None


# ---- reducer tests ----

from harness.tui.render import RenderedItem
from harness.tui.state import (
    reduce, TurnStarted, TurnEnded, ItemReceived, TokensUpdated,
    PermissionOpened, PermissionClosed,
)


def _active(fs):
    return fs.active


def test_turn_started_goes_thinking():
    fs = reduce(initial_snapshot(), TurnStarted())
    assert _active(fs).state == AgentState.THINKING


def test_message_item_goes_responding():
    fs = initial_snapshot()
    fs = reduce(fs, TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="message", text="hi")))
    assert _active(fs).state == AgentState.RESPONDING


def test_tool_item_sets_tool_and_task():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ pytest tests/", status="pending")))
    a = _active(fs)
    assert a.state == AgentState.RUNNING_TOOL
    assert a.tool is not None
    assert a.tool.subtype == "test"
    assert a.tool.status == ToolStatus.PENDING
    assert len(a.tasks) == 1 and a.tasks[0].status == "in_progress"


def test_tool_update_completes_task():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ echo hi", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1",
                                              status="completed", body="hi")))
    a = _active(fs)
    assert a.tool.status == ToolStatus.DONE
    assert a.tasks[0].status == "done"


def test_tokens_update():
    fs = reduce(initial_snapshot(), TokensUpdated(1234))
    assert _active(fs).tokens == 1234


def test_permission_open_close():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ echo", status="pending")))
    fs = reduce(fs, PermissionOpened())
    assert _active(fs).state == AgentState.AWAITING_PERMISSION
    fs = reduce(fs, PermissionClosed())
    assert _active(fs).state == AgentState.RUNNING_TOOL


def test_turn_ended_ok_and_fail():
    ok = reduce(reduce(initial_snapshot(), TurnStarted()), TurnEnded(ok=True))
    assert _active(ok).state == AgentState.DONE
    bad = reduce(reduce(initial_snapshot(), TurnStarted()), TurnEnded(ok=False))
    assert _active(bad).state == AgentState.FAILED


def test_reduce_is_pure_returns_new_object():
    fs0 = initial_snapshot()
    fs1 = reduce(fs0, TurnStarted())
    assert fs0.active.state == AgentState.IDLE   # original unchanged
    assert fs1 is not fs0
