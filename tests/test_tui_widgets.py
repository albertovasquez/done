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
    # assert on the markup string passed to update() — stored as _Static__content
    markup = chip._Static__content
    assert "RUNNING" in markup
    assert "[b]" in markup          # bold marker is present in the markup
    assert "RUNNING" in chip._label


def test_status_chip_for_yolo_off_is_muted_bypass_off():
    chip = StatusChip.for_yolo(active=False, pinned=False)
    markup = chip._Static__content
    assert "bypass permissions off" in chip._label
    assert "$muted" in markup                  # safe state is quiet, not loud


def test_status_chip_for_yolo_on_is_red_bypass_on():
    chip = StatusChip.for_yolo(active=True, pinned=False)
    assert "bypass permissions on" in chip._label
    assert "pinned" not in chip._label
    assert "$error" in chip._Static__content   # RED — loudest signal for a full bypass
    assert "▶▶" in chip._label                 # the bypass glyph


def test_status_chip_for_yolo_pinned_shows_pinned_marker():
    chip = StatusChip.for_yolo(active=True, pinned=True)
    assert "bypass permissions on" in chip._label and "pinned" in chip._label
    assert "$error" in chip._Static__content


def test_activity_glyph_reduced_motion_is_static():
    # Attribute-level assertion is the unit-test ceiling here: on_mount (which
    # sets up the timer and initial display) only runs inside a mounted Textual
    # app, which requires async infrastructure. We verify the flag is recorded
    # so that on_mount will skip the timer branch.
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


def test_activity_status_reduced_motion_disables_animation():
    w = ActivityStatus(reduced_motion=True)
    assert w._reduced_motion is True


def test_activity_status_shows_done_tool_count():
    from harness.tui.state import ToolView, ToolStatus
    w = ActivityStatus()
    tools = (
        ToolView(title="$ a", status=ToolStatus.DONE, subtype="shell", id="1"),
        ToolView(title="$ b", status=ToolStatus.DONE, subtype="shell", id="2"),
        ToolView(title="$ c", status=ToolStatus.ACTIVE, subtype="shell", id="3"),
    )
    snap = AgentSnapshot(id="default", name="agent", state=AgentState.RUNNING_TOOL,
                         activity_label="Running tool", elapsed=10.0, tools=tools)
    assert "2 done" in w.line_for(snap)


def test_activity_status_hides_count_when_no_done_tools():
    from harness.tui.state import ToolView, ToolStatus
    w = ActivityStatus()
    tools = (ToolView(title="$ a", status=ToolStatus.ACTIVE, subtype="shell", id="1"),)
    snap = AgentSnapshot(id="default", name="agent", state=AgentState.RUNNING_TOOL,
                         activity_label="Running tool", elapsed=10.0, tools=tools)
    assert "done" not in w.line_for(snap)


from harness.tui.state import TaskItem, ToolView, ToolStatus
from harness.tui.widgets.task_tree import TaskTree
from harness.tui.widgets.tool_call_row import ToolCallRow, cap_body


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


def test_cap_body_caps_lines():
    body = "\n".join(f"line{i}" for i in range(20))
    assert cap_body(body, "read").count("\n") <= 6
    assert cap_body(body, "shell").count("\n") <= 10
    assert cap_body("", "shell") == ""


def test_tool_call_row_detail_includes_body():
    tool = ToolView(title="$ cat f.py", status=ToolStatus.DONE, subtype="read",
                    body="alpha\nbeta", id="t1")
    row = ToolCallRow(tool, expanded=True)
    detail = row.detail_for(tool)
    assert "f.py" in detail
    assert "alpha" in detail and "beta" in detail


def test_tool_call_row_collapsed_line_unchanged():
    tool = ToolView(title="$ pytest", status=ToolStatus.ACTIVE, subtype="test", id="t1")
    row = ToolCallRow(tool)
    assert "⚑" in row.line_for(tool) and "pytest" in row.line_for(tool)


from harness.tui.state import DecisionView
from harness.tui.widgets.decision_prompt import (
    DecisionPrompt, TYPE_SOMETHING, CHAT_ABOUT_IT,
)


def test_decision_prompt_option_lines():
    dv = DecisionView(question="Where should the seam live?",
                      options=(("Wrapper", "isolated, recommended"),
                               ("Patch upstream", "violates zero-edits")))
    dp = DecisionPrompt(dv)
    lines = dp.option_lines()
    # numbered options + 2 fallbacks
    assert any("1." in ln and "Wrapper" in ln for ln in lines)
    assert any("isolated" in ln for ln in lines)
    assert any("Type something" in ln for ln in lines)
    assert any("Chat about this" in ln for ln in lines)


# --- Finding (1): DecisionPrompt cursor + option_lines ---

def _make_dp(n: int = 2) -> DecisionPrompt:
    options = tuple((f"Opt{i}", f"rationale {i}") for i in range(n))
    dv = DecisionView(question="Q?", options=options)
    return DecisionPrompt(dv)


def test_decision_prompt_cursor_marker_at_start():
    dp = _make_dp(2)
    # cursor starts at 0; first option line should have the › prefix
    lines = dp.option_lines()
    assert any("› " in ln and "Opt0" in ln for ln in lines)
    # other rows should not have › prefix on their title lines
    assert not any("› " in ln and "Opt1" in ln for ln in lines)


def test_decision_prompt_move_updates_cursor():
    dp = _make_dp(2)
    dp.move(1)
    assert dp._cursor == 1
    lines = dp.option_lines()
    assert any("› " in ln and "Opt1" in ln for ln in lines)
    assert not any("› " in ln and "Opt0" in ln for ln in lines)


def test_decision_prompt_move_clamps_at_bottom():
    dp = _make_dp(2)
    # n=2, total=4; clamp at 3
    dp.move(100)
    assert dp._cursor == 3


def test_decision_prompt_move_clamps_at_top():
    dp = _make_dp(2)
    dp.move(-100)
    assert dp._cursor == 0


def test_decision_prompt_select_cursor_0():
    """cursor at 0 → Selected(0)"""
    dp = _make_dp(2)
    dp._cursor = 0
    messages: list = []
    dp.post_message = lambda m: messages.append(m)
    dp.select()
    assert len(messages) == 1
    assert messages[0].index == 0


def test_decision_prompt_select_cursor_at_n():
    """cursor at n (first fallback) → Selected(TYPE_SOMETHING)"""
    dp = _make_dp(2)
    dp._cursor = dp._n  # == 2
    messages: list = []
    dp.post_message = lambda m: messages.append(m)
    dp.select()
    assert messages[0].index == TYPE_SOMETHING


def test_decision_prompt_select_cursor_at_n_plus_1():
    """cursor at n+1 (second fallback) → Selected(CHAT_ABOUT_IT)"""
    dp = _make_dp(2)
    dp._cursor = dp._n + 1  # == 3
    messages: list = []
    dp.post_message = lambda m: messages.append(m)
    dp.select()
    assert messages[0].index == CHAT_ABOUT_IT


def test_decision_prompt_fallback_cursor_markers():
    dp = _make_dp(2)
    # Move to Type something (index n=2)
    dp.move(dp._n)
    lines = dp.option_lines()
    assert any("› " in ln and "Type something" in ln for ln in lines)
    # Move to Chat about this
    dp.move(1)
    lines = dp.option_lines()
    assert any("› " in ln and "Chat about this" in ln for ln in lines)


# --- Finding (5): StateDot tests ---

def test_state_dot_constructs_without_keyerror():
    from harness.tui.tokens import GLYPH
    for state in AgentState:
        dot = StateDot(state)   # must not raise KeyError
        # _Static__content holds the markup string passed to update()
        markup = dot._Static__content
        assert any(g in markup for g in GLYPH.values()), \
            f"No known glyph found in StateDot markup for {state}"


# --- Finding (4): TOOL_STATUS_TOKEN / TOOL_STATUS_LABEL exported from status_chip ---

def test_tool_status_dicts_exported():
    from harness.tui.widgets.status_chip import TOOL_STATUS_TOKEN, TOOL_STATUS_LABEL
    assert TOOL_STATUS_TOKEN[ToolStatus.PENDING] == "scheduled"
    assert TOOL_STATUS_TOKEN[ToolStatus.ACTIVE] == "accent"
    assert TOOL_STATUS_TOKEN[ToolStatus.DONE] == "success"
    assert TOOL_STATUS_TOKEN[ToolStatus.FAILED] == "error"
    assert TOOL_STATUS_LABEL[ToolStatus.PENDING] == "QUEUED"
    assert TOOL_STATUS_LABEL[ToolStatus.ACTIVE] == "RUNNING"
    assert TOOL_STATUS_LABEL[ToolStatus.DONE] == "COMPLETED"
    assert TOOL_STATUS_LABEL[ToolStatus.FAILED] == "FAILED"


def test_activity_status_single_ellipsis():
    w = ActivityStatus()
    snap = AgentSnapshot(id="default", name="agent", state=AgentState.RESPONDING,
                         activity_label="Responding", elapsed=5.0, tokens=0)
    line = w.line_for(snap)
    assert "……" not in line, f"double ellipsis: {line!r}"
    assert "Responding…" in line


def test_activity_status_hides_zero_tokens():
    w = ActivityStatus()
    snap0 = AgentSnapshot(id="default", name="agent", state=AgentState.RESPONDING,
                          activity_label="Responding", elapsed=5.0, tokens=0)
    assert "tokens" not in w.line_for(snap0), "0 tokens must be hidden"
    snapN = AgentSnapshot(id="default", name="agent", state=AgentState.RESPONDING,
                          activity_label="Responding", elapsed=5.0, tokens=1500)
    assert "tokens" in w.line_for(snapN), "nonzero tokens must show"


# --- ActivityRegion ---

import asyncio
from harness.tui.widgets.activity_region import ActivityRegion


def _working_snap():
    return AgentSnapshot(
        id="default", name="agent", state=AgentState.RUNNING_TOOL,
        activity_label="Running test", elapsed=4.0, tokens=0,
        tasks=(TaskItem(label="$ pytest", status="in_progress"),),
        tools=(ToolView(title="$ pytest", status=ToolStatus.ACTIVE, subtype="test",
                        body="ran 3 tests", id="t1"),),
        tool=ToolView(title="$ pytest", status=ToolStatus.ACTIVE, subtype="test", id="t1"),
    )


def test_activity_region_idle_helper():
    region = ActivityRegion()
    idle = AgentSnapshot(id="default", name="agent", state=AgentState.IDLE)
    done = AgentSnapshot(id="default", name="agent", state=AgentState.DONE)
    assert region.is_idle(idle) and region.is_idle(done)
    assert not region.is_idle(_working_snap())


def test_activity_region_mounts_and_shows_tool_when_working():
    class Host(App):
        def compose(self) -> ComposeResult:
            yield ActivityRegion(id="activity-region")
    async def go():
        async with Host().run_test() as pilot:
            region = pilot.app.query_one("#activity-region", ActivityRegion)
            region.update_from(_working_snap())
            await pilot.pause()
            # default view: status line only — the TaskTree command list is hidden
            from harness.tui.widgets.task_tree import TaskTree
            task_tree = region.query_one("#ar-tasks", TaskTree)
            assert task_tree.display is False, "TaskTree must be hidden in default view"
            # toggle to detail: ToolCallRow(s) appear
            region.toggle_details()
            region.update_from(_working_snap())
            await pilot.pause()
            from harness.tui.widgets.tool_call_row import ToolCallRow
            assert region.query(ToolCallRow), "ToolCallRow should appear when expanded"
    asyncio.run(go())


def test_cap_body_caps_lines():
    body = "\n".join(f"line{i}" for i in range(20))
    assert cap_body(body, "read").count("\n") <= 6
    assert cap_body(body, "shell").count("\n") <= 10
    assert cap_body("", "shell") == ""


def test_tool_call_row_detail_includes_body():
    tool = ToolView(title="$ cat f.py", status=ToolStatus.DONE, subtype="read",
                    body="alpha\nbeta", id="t1")
    row = ToolCallRow(tool, expanded=True)
    detail = row.detail_for(tool)
    assert "f.py" in detail
    assert "alpha" in detail and "beta" in detail


def test_tool_call_row_collapsed_line_unchanged():
    tool = ToolView(title="$ pytest", status=ToolStatus.ACTIVE, subtype="test", id="t1")
    row = ToolCallRow(tool)
    assert "⚑" in row.line_for(tool) and "pytest" in row.line_for(tool)
