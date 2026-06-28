from harness.tui.state import (
    AgentState, ToolStatus, ToolView, TaskItem, ScheduleView, DecisionView,
    AgentSnapshot, FleetSnapshot, initial_snapshot,
    infer_subtype, strip_done_sentinel_prose,
    persona_from_meta, PersonaResolved, reduce, _reduce_agent,
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
    # First PersonaResolved("fred") seeds a new agent (initial snapshot has "default").
    snap = reduce(initial_snapshot(), PersonaResolved("fred"))
    assert snap.active_id == "fred"
    agent_count = len(snap.agents)            # 2: "default" + "fred"
    # Second PersonaResolved("fred") selects the existing "fred" — no new agent seeded.
    again = reduce(snap, PersonaResolved("fred"))
    assert again.active_id == "fred"
    assert again.active.id == "fred"
    assert len(again.agents) == agent_count   # idempotent: no duplicate agent added

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


def test_infer_subtype_classifies_file_tools():
    # the new Read/Write/Edit tools' display labels start with the verb
    assert infer_subtype("read harness/api.py") == "read"
    assert infer_subtype("edit harness/api.py") == "edit"
    assert infer_subtype("write harness/api.py") == "edit"   # closest shipped glyph (✎)


def test_infer_subtype_file_tools_do_not_break_shell_first_words():
    # regression: the new verbs must not steal classification from real shell cmds
    assert infer_subtype("grep -rn foo") == "search"
    assert infer_subtype("cat README.md") == "read"
    assert infer_subtype("echo hi") == "shell"


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


def test_done_sentinel_tool_is_suppressed():
    # The agent finishes every turn by running `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`
    # (acp_emit prefixes the title with "$ "). That protocol artifact must NOT show up
    # as a tool row / task in the activity region — it is not user-facing work.
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(
        kind="tool", id="done",
        title="$ echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", status="pending")))
    a = _active(fs)
    assert a.tools == ()                       # no tool row
    assert a.tasks == ()                       # no task entry
    assert a.tool is None                      # no active tool
    assert a.state != AgentState.RUNNING_TOOL  # state untouched by the sentinel


def test_done_sentinel_update_is_noop():
    # The matching tool_update (status flip for the suppressed sentinel id) must also
    # be a no-op — it has no row to update and must not resurrect one.
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(
        kind="tool", id="done",
        title="$ echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(
        kind="tool_update", id="done", status="completed", body="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")))
    a = _active(fs)
    assert a.tools == () and a.tasks == ()


def test_non_sentinel_echo_still_renders():
    # Scope guard: only the EXACT sentinel command is hidden. Any other echo the
    # agent runs is real work and must render normally.
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(
        kind="tool", id="e1", title="$ echo hello world", status="pending")))
    a = _active(fs)
    assert len(a.tools) == 1


_SENTINEL = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def test_strip_sentinel_prose_standalone_trailing_line():
    # The reported leak: a model TYPES the sentinel as the final line of its
    # answer (the tool-row guard never sees a typed line). Drop it — and the
    # blank separator line it sat behind — so the answer ends cleanly.
    buf = f"Here is the answer.\n\n{_SENTINEL}"
    assert strip_done_sentinel_prose(buf) == "Here is the answer."


def test_strip_sentinel_prose_with_trailing_newline():
    buf = f"Done.\n\n{_SENTINEL}\n"
    assert strip_done_sentinel_prose(buf) == "Done."


def test_strip_sentinel_prose_midbuffer_line():
    # Sentinel on its own line in the MIDDLE of the buffer — drop the line, keep
    # the prose on both sides intact.
    buf = f"Before.\n{_SENTINEL}\nAfter."
    assert strip_done_sentinel_prose(buf) == "Before.\nAfter."


def test_strip_sentinel_prose_split_across_chunks():
    # Deltas can split the sentinel. A trailing PARTIAL (buffer ends mid-command)
    # is held back so it never flickers; once the rest arrives the whole line goes.
    partial = f"Answer.\n\necho COMPLETE_TASK_AND_"
    assert strip_done_sentinel_prose(partial) == "Answer."
    full = f"Answer.\n\n{_SENTINEL}"
    assert strip_done_sentinel_prose(full) == "Answer."


def test_strip_sentinel_prose_handles_fencing():
    # Models wrap it in backticks or a bullet — match through those wrappers.
    assert strip_done_sentinel_prose(f"x\n`{_SENTINEL}`") == "x"
    assert strip_done_sentinel_prose(f"x\n- {_SENTINEL}") == "x"
    assert strip_done_sentinel_prose(f"x\n> {_SENTINEL}") == "x"


def test_strip_sentinel_prose_preserves_inline_mention():
    # Scope guard: a sentence that MENTIONS the command (not a standalone line)
    # is real prose and must survive untouched.
    buf = f"I finish each turn by running {_SENTINEL} as a shell command."
    assert strip_done_sentinel_prose(buf) == buf


def test_strip_sentinel_prose_noop_when_absent():
    buf = "A normal multi-line\nanswer with no sentinel at all."
    assert strip_done_sentinel_prose(buf) == buf


def test_tool_update_completes_task():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ echo hi", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool_update", id="t1",
                                              status="completed", body="hi")))
    a = _active(fs)
    assert a.tool.status == ToolStatus.DONE
    assert a.tasks[0].status == "done"


def test_plan_item_sets_plan_field():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan", entries=(
        ("Push + PR", "in_progress"),
        ("CI + merge", "pending"),
        ("Sync + prune", "completed"),
    ))))
    a = _active(fs)
    assert [(t.label, t.status) for t in a.plan] == [
        ("Push + PR", "in_progress"),
        ("CI + merge", "pending"),
        ("Sync + prune", "done"),
    ]
    assert all(t.tool_id == "" for t in a.plan)


def test_plan_update_replaces_not_appends():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan",
        entries=(("A", "in_progress"), ("B", "pending")))))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan",
        entries=(("A", "completed"), ("B", "in_progress")))))
    a = _active(fs)
    assert [(t.label, t.status) for t in a.plan] == [("A", "done"), ("B", "in_progress")]


def test_plan_does_not_touch_tasks():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ echo hi", status="pending")))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan",
        entries=(("Step one", "in_progress"),))))
    a = _active(fs)
    assert len(a.tasks) == 1 and a.tasks[0].label == "$ echo hi"   # tool task untouched
    assert len(a.plan) == 1 and a.plan[0].label == "Step one"


def test_turn_started_clears_plan():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, ItemReceived(RenderedItem(kind="plan",
        entries=(("Step one", "in_progress"),))))
    assert len(_active(fs).plan) == 1
    fs = reduce(fs, TurnStarted())
    assert _active(fs).plan == ()


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


def test_decision_opened_none_clears_and_leaves_awaiting_state():
    dv = DecisionView(question="q?", options=(("a", "b"),))
    fs = reduce(initial_snapshot(), DecisionOpened(dv))
    assert fs.active.state == AgentState.AWAITING_DECISION
    cleared = reduce(fs, DecisionOpened(None))
    assert cleared.active.decision is None
    # no live tool → falls back to RESPONDING, NOT stuck in AWAITING_DECISION
    assert cleared.active.state == AgentState.RESPONDING


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


# ---- PersonaResolved select/seed invariant tests (C2c task 4) ----

def test_persona_resolved_no_duplicate_ids():
    snap = FleetSnapshot(agents=(AgentSnapshot(id="a", name="a"),
                                 AgentSnapshot(id="b", name="b")), active_id="a")
    out = reduce(snap, PersonaResolved("b"))
    ids = [ag.id for ag in out.agents]
    assert len(ids) == len(set(ids))                 # no dup
    assert out.active_id == "b"
    assert sum(ag.id == "b" for ag in out.agents) == 1


def test_persona_resolved_preserves_target_state():
    snap = FleetSnapshot(
        agents=(AgentSnapshot(id="a", name="a", tokens=10),
                AgentSnapshot(id="b", name="b", tokens=99)), active_id="a")
    out = reduce(snap, PersonaResolved("b"))
    b = next(ag for ag in out.agents if ag.id == "b")
    assert b.tokens == 99                            # selected, NOT overwritten with a's


def test_persona_resolved_seeds_when_absent():
    snap = FleetSnapshot(agents=(AgentSnapshot(id="a", name="a"),), active_id="a")
    out = reduce(snap, PersonaResolved("c"))
    assert out.active_id == "c"
    assert any(ag.id == "c" for ag in out.agents)
    assert out.active is not None


# ---- TurnEnded is terminal: a late item must not resurrect a working state ----
# Regression for the stuck "Responding…" spinner. The prompt() RPC response can
# resolve (→ TurnEnded) BEFORE the agent's last message-chunk session_update
# notifications drain on Textual's queue, so the proven _apply order is
# [TurnStarted, TurnEnded, ItemReceived(message)…]. The late item must leave the
# activity state terminal, else the ActivityRegion sticks on "Responding" forever.

def test_late_message_after_turn_end_stays_done():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, TurnEnded(ok=True))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="message", text="trailing delta")))
    a = _active(fs)
    assert a.state == AgentState.DONE          # NOT resurrected to RESPONDING
    assert a.activity_label == ""


def test_late_tool_after_turn_end_stays_done():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, TurnEnded(ok=True))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="tool", id="t1",
                                              title="$ echo late", status="pending")))
    assert _active(fs).state == AgentState.DONE  # not resurrected to RUNNING_TOOL


def test_late_item_after_failed_turn_stays_failed():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, TurnEnded(ok=False))
    fs = reduce(fs, ItemReceived(RenderedItem(kind="message", text="trailing delta")))
    assert _active(fs).state == AgentState.FAILED


def test_new_turn_after_done_still_responds():
    # the guard must NOT wedge the next turn: TurnStarted clears the terminal
    # state first, so its first message correctly goes RESPONDING.
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = reduce(fs, TurnEnded(ok=True))
    fs = reduce(fs, TurnStarted())                         # next turn
    fs = reduce(fs, ItemReceived(RenderedItem(kind="message", text="hi")))
    assert _active(fs).state == AgentState.RESPONDING


# ---- Phase-labeled liveness tests (C4) ----

from dataclasses import dataclass

@dataclass
class _FakeItem:
    kind: str


def test_turn_start_label_is_classifying():
    a = AgentSnapshot(id="a", name="x")
    a = _reduce_agent(a, TurnStarted())
    assert a.activity_label == "Classifying…"
    assert a.state == AgentState.THINKING


def test_first_message_chunk_flips_to_responding():
    a = _reduce_agent(AgentSnapshot(id="a", name="x"), TurnStarted())
    a = _reduce_agent(a, ItemReceived(item=_FakeItem(kind="message")))
    assert a.activity_label == "Responding"
    assert a.state == AgentState.RESPONDING
