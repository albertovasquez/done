import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.state import (
    AgentState, ToolStatus, ToolView, TaskItem, ScheduleView, DecisionView,
    AgentSnapshot, FleetSnapshot, initial_snapshot,
    infer_subtype,
    persona_from_meta, PersonaResolved, reduce,
)


def test_persona_from_meta_reads_id():
    assert persona_from_meta({"harness": {"persona": {"id": "fred"}}}) == "fred"

def test_persona_from_meta_tolerant_of_garbage():
    assert persona_from_meta(None) is None
    assert persona_from_meta({}) is None
    assert persona_from_meta({"harness": "nope"}) is None
    assert persona_from_meta({"harness": {"persona": {}}}) is None          # no id
    assert persona_from_meta({"harness": {"persona": {"id": 5}}}) is None   # non-str id

def test_reduce_persona_sets_active_id_and_remaps_agent():
    snap = initial_snapshot()                 # active_id="default", agent ("default","agent")
    out = reduce(snap, PersonaResolved("fred"))
    assert out.active_id == "fred"
    assert out.active is not None
    assert out.active.id == "fred"
    assert out.active.name == "fred"

def test_reduce_persona_idempotent():
    snap = reduce(initial_snapshot(), PersonaResolved("fred"))
    again = reduce(snap, PersonaResolved("fred"))
    assert again.active_id == "fred"
    assert again.active.id == "fred"
    assert len(again.agents) == 1             # no duplicate agent added

def test_reduce_persona_invariant_active_never_none():
    # Codex edge case: if active_id matches NO agent (reachable once C2c holds
    # multiple agents), PersonaResolved must still leave .active resolvable — it
    # seeds an agent for the new id rather than producing active_id with no agent.
    snap = FleetSnapshot(
        agents=(AgentSnapshot(id="a", name="A"), AgentSnapshot(id="b", name="B")),
        active_id="missing")              # active is None here
    assert snap.active is None
    out = reduce(snap, PersonaResolved("fred"))
    assert out.active_id == "fred"
    assert out.active is not None         # invariant restored
    assert out.active.id == "fred" and out.active.name == "fred"
    # the pre-existing agents are untouched
    assert any(a.id == "a" for a in out.agents) and any(a.id == "b" for a in out.agents)


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


# ---- decision tests ----

from harness.tui.state import decision_from_meta, DecisionOpened


def test_decision_from_meta_parses():
    fm = {"harness": {"decision": {
        "question": "Where should the seam live?",
        "options": [
            {"title": "Wrapper", "rationale": "isolated"},
            {"title": "Patch upstream", "rationale": "violates zero-edits"},
        ]}}}
    dv = decision_from_meta(fm)
    assert dv is not None
    assert dv.question.startswith("Where")
    assert dv.options[0] == ("Wrapper", "isolated")


def test_decision_from_meta_malformed_returns_none():
    assert decision_from_meta(None) is None
    assert decision_from_meta({}) is None
    assert decision_from_meta({"harness": {"decision": {}}}) is None
    assert decision_from_meta({"harness": "x"}) is None


def test_decision_opened_sets_state():
    dv = DecisionView(question="q?", options=(("a", "b"),))
    fs = reduce(initial_snapshot(), DecisionOpened(dv))
    a = fs.active
    assert a.state == AgentState.AWAITING_DECISION
    assert a.decision == dv


def test_tool_update_targets_live_tool_not_last_index():
    """tool_update must update the task matching the CURRENT live tool (a.tool.title),
    not the last task by index.

    This test constructs a state where the live tool (a.tool) is NOT the last task
    by index: two tool tasks exist, but the live tool corresponds to the FIRST task
    (index 0), while an extra in_progress task sits at the end (index 1).
    The buggy index-based code would update index 1 (wrong); the correct label-based
    code must update only the task whose label matches a.tool.title.
    """
    from harness.tui.state import ToolView, _reduce_agent, AgentSnapshot
    from dataclasses import replace as dc_replace

    # Build a synthetic state: two tasks, but live tool title = first task's label
    # (simulates a task added for a different reason after the tool was set)
    live_tool = ToolView(title="$ echo one", status=ToolStatus.PENDING, subtype="shell", id="t1")
    synthetic_agent = AgentSnapshot(
        id="default",
        name="agent",
        state=AgentState.RUNNING_TOOL,
        tool=live_tool,
        tools=(live_tool,),
        tasks=(
            TaskItem(label="$ echo one", status="in_progress", tool_id="t1"),  # index 0 — matches live tool by id
            TaskItem(label="$ other task", status="in_progress", tool_id="t2"),  # index 1 — different id
        ),
    )
    synthetic_fs = FleetSnapshot(agents=(synthetic_agent,), active_id="default")

    # tool_update: completed for the LIVE tool ("$ echo one")
    fs2 = reduce(synthetic_fs, ItemReceived(RenderedItem(kind="tool_update", id="t1",
                                                          status="completed", body="")))
    a = _active(fs2)
    # Task matching the live tool (index 0, "$ echo one") must be done
    assert a.tasks[0].status == "done", (
        f"expected tasks[0] (live tool match) to be done, got {a.tasks[0].status!r}"
    )
    # Task at last index (index 1, "$ other task") must remain in_progress
    assert a.tasks[1].status == "in_progress", (
        f"expected tasks[1] (non-live-tool) to remain in_progress, got {a.tasks[1].status!r}"
    )


def test_tool_update_propagates_body():
    """tool_update with a body must set the matching ToolView's body."""
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ cat f", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1", status="completed", body="hello\nworld")))
    tv = _active(fs).tool
    assert tv is not None
    assert tv.body == "hello\nworld"


def test_permission_closed_restores_responding_when_no_live_tool():
    """PermissionClosed with no live tool must restore RESPONDING state."""
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="message", text="x")))
    assert _active(fs).state == AgentState.RESPONDING
    assert _active(fs).tool is None
    fs = reduce(fs, PermissionOpened())
    assert _active(fs).state == AgentState.AWAITING_PERMISSION
    fs = reduce(fs, PermissionClosed())
    assert _active(fs).state == AgentState.RESPONDING


def test_reducer_tracks_multiple_tools_by_id():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ echo one", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t2", title="$ pytest two", status="pending")))
    a = fs.active
    assert len(a.tools) == 2
    assert a.tools[0].id == "t1" and a.tools[1].id == "t2"
    # update the FIRST tool — must update t1, NOT the latest (t2)
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1", status="completed", body="hi")))
    a = fs.active
    by_id = {tv.id: tv for tv in a.tools}
    assert by_id["t1"].status == ToolStatus.DONE, "t1 should be DONE"
    assert by_id["t2"].status == ToolStatus.PENDING, "t2 must stay PENDING (not clobbered)"


def test_turn_started_resets_tools():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ x", status="pending")))
    assert len(fs.active.tools) == 1
    fs = reduce(fs, TurnStarted())
    assert fs.active.tools == (), "TurnStarted must reset tools"


def test_tool_update_propagates_body():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ cat f", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1", status="completed", body="hello\nworld")))
    a = _active(fs)
    assert a.tools[0].body == "hello\nworld"
    assert a.tool.body == "hello\nworld"


def test_tool_update_without_body_keeps_prior_body():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ x", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1", status="active", body="partial")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1", status="completed", body="")))
    a = _active(fs)
    assert a.tools[0].body == "partial", "a body-less update must not wipe the prior body"


def test_tool_update_same_title_updates_only_matching_task():
    """Two tools with the SAME title but different ids: a tool_update for one must
    flip only THAT task row, not both (match by tool_id, not label)."""
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ cat f", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t2", title="$ cat f", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1", status="completed", body="x")))
    a = _active(fs)
    assert a.tasks[0].status == "done", "t1's task row should be done"
    assert a.tasks[1].status == "in_progress", "t2's identically-titled task row must NOT flip"


def test_tool_update_blank_id_is_noop():
    """An update with an empty id must not clobber default-id tools/tasks."""
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1", title="$ x", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="", status="completed", body="y")))
    a = _active(fs)
    assert a.tools[0].status == ToolStatus.PENDING, "blank-id update must not change the tool"
    assert a.tasks[0].status == "in_progress", "blank-id update must not change the task"
