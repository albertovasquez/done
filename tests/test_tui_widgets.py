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


# Footer chips collapse: glyph-when-ON (the expected default), full labeled chip
# when OFF (the surprising state). See test_status_chip_collapse.py for the full
# rule; these keep the widget-level colour/markup assertions.
def test_status_chip_for_yolo_off_is_muted_bypass_off():
    chip = StatusChip.for_yolo(active=False, pinned=False)
    assert "bypass OFF" in chip._label
    assert "$muted" in chip._Static__content   # safe/off state is quiet, not loud


def test_status_chip_for_yolo_on_is_red_glyph():
    chip = StatusChip.for_yolo(active=True, pinned=False)
    assert chip._label == "▶▶"                 # ON collapses to the bare glyph
    assert "bypass" not in chip._label
    assert "$error" in chip._Static__content   # RED — loudest signal for a full bypass


def test_status_chip_for_yolo_pinned_still_glyph_only():
    # pinned is a persistence detail; ON stays terse (colour carries the signal).
    chip = StatusChip.for_yolo(active=True, pinned=True)
    assert chip._label == "▶▶"
    assert "$error" in chip._Static__content


def test_status_chip_compress_aware_off_is_muted():
    chip = StatusChip.for_compress_aware(active=False, pinned=False)
    assert "compress-aware OFF" in chip._label
    assert "$muted" in chip._Static__content


def test_status_chip_compress_aware_on_is_glyph():
    chip = StatusChip.for_compress_aware(active=True, pinned=False)
    assert chip._label == "▤"                  # ON collapses to the bare glyph
    assert "compress-aware" not in chip._label
    assert "$error" not in chip._Static__content    # NOT the danger color


def test_status_chip_compress_aware_pinned_still_glyph_only():
    chip = StatusChip.for_compress_aware(active=True, pinned=True)
    assert chip._label == "▤"


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
from harness.tui.widgets.decision_modal import (
    DecisionModal, TYPE_SOMETHING, CHAT_ABOUT_IT,
)


def test_decision_modal_option_lines():
    dv = DecisionView(question="Where should the seam live?",
                      options=(("Wrapper", "isolated, recommended"),
                               ("Patch upstream", "violates zero-edits")))
    dm = DecisionModal(dv)
    lines = dm.option_lines()
    # numbered options + 2 fallbacks
    assert any("1." in ln and "Wrapper" in ln for ln in lines)
    assert any("isolated" in ln for ln in lines)
    assert any("Type something" in ln for ln in lines)
    assert any("Chat about this" in ln for ln in lines)


def test_decision_modal_marks_first_option_recommended():
    dv = DecisionView(question="Q?",
                      options=(("Best", "do this"), ("Other", "maybe")))
    dm = DecisionModal(dv)
    lines = dm.option_lines()
    # option 1 carries a (recommended) marker; option 2 does not
    assert any("Best" in ln and "(recommended)" in ln for ln in lines)
    assert not any("Other" in ln and "(recommended)" in ln for ln in lines)


# --- DecisionModal cursor + option_lines ---

def _make_dm(n: int = 2) -> DecisionModal:
    options = tuple((f"Opt{i}", f"rationale {i}") for i in range(n))
    dv = DecisionView(question="Q?", options=options)
    return DecisionModal(dv)


def test_decision_modal_cursor_marker_at_start():
    dm = _make_dm(2)
    # cursor starts at 0; first option line should have the › prefix
    lines = dm.option_lines()
    assert any("› " in ln and "Opt0" in ln for ln in lines)
    # other rows should not have › prefix on their title lines
    assert not any("› " in ln and "Opt1" in ln for ln in lines)


def test_decision_modal_move_updates_cursor():
    dm = _make_dm(2)
    dm.move(1)
    assert dm._cursor == 1
    lines = dm.option_lines()
    assert any("› " in ln and "Opt1" in ln for ln in lines)
    assert not any("› " in ln and "Opt0" in ln for ln in lines)


def test_decision_modal_move_clamps_at_bottom():
    dm = _make_dm(2)
    # n=2, total=4; clamp at 3
    dm.move(100)
    assert dm._cursor == 3


def test_decision_modal_move_clamps_at_top():
    dm = _make_dm(2)
    dm.move(-100)
    assert dm._cursor == 0


def test_decision_modal_select_cursor_0_dismisses_with_index():
    """cursor at 0 → dismiss(0)"""
    dm = _make_dm(2)
    dm._cursor = 0
    dismissed: list = []
    dm.dismiss = lambda v=None: dismissed.append(v)
    dm.select()
    assert dismissed == [0]


def test_decision_modal_select_cursor_at_n_dismisses_type_something():
    """cursor at n (first fallback) → dismiss(TYPE_SOMETHING)"""
    dm = _make_dm(2)
    dm._cursor = dm._n  # == 2
    dismissed: list = []
    dm.dismiss = lambda v=None: dismissed.append(v)
    dm.select()
    assert dismissed == [TYPE_SOMETHING]


def test_decision_modal_select_cursor_at_n_plus_1_dismisses_chat():
    """cursor at n+1 (second fallback) → dismiss(CHAT_ABOUT_IT)"""
    dm = _make_dm(2)
    dm._cursor = dm._n + 1  # == 3
    dismissed: list = []
    dm.dismiss = lambda v=None: dismissed.append(v)
    dm.select()
    assert dismissed == [CHAT_ABOUT_IT]


def test_decision_modal_cancel_dismisses_with_none():
    dm = _make_dm(2)
    dismissed: list = []
    dm.dismiss = lambda v=None: dismissed.append(v)
    dm.action_cancel()
    assert dismissed == [None]


def test_decision_modal_fallback_cursor_markers():
    dm = _make_dm(2)
    # Move to Type something (index n=2)
    dm.move(dm._n)
    lines = dm.option_lines()
    assert any("› " in ln and "Type something" in ln for ln in lines)
    # Move to Chat about this
    dm.move(1)
    lines = dm.option_lines()
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


# Stub for _check_proxy_config_drift: the method now reads self._shell_neuralwatt_key
# (the pre-load_env shell snapshot captured in tui_main.py) instead of trusting
# config_drift()'s os.environ-derived default. Every stub below carries the
# attribute so `self._shell_neuralwatt_key` resolves; None means "the shell did
# not genuinely export NEURALWATT_API_KEY", matching tui_main.py's capture.
class _DriftStub:
    def __init__(self, shell_neuralwatt_key=None):
        self._shell_neuralwatt_key = shell_neuralwatt_key
        self.logged = []

    def log(self, msg):
        self.logged.append(msg)


def test_check_proxy_config_drift_logs_when_drifted(monkeypatch):
    from harness.tui import app as app_mod

    monkeypatch.setattr(
        "harness.proxy_service.config_gen.config_drift", lambda env=None: "drifted"
    )
    stub = _DriftStub()
    prompted = []
    stub._show_proxy_refresh_prompt = lambda: prompted.append(True)

    app_mod.HarnessTui._check_proxy_config_drift(stub)
    assert prompted == [True]
    assert stub.logged == []


def test_check_proxy_config_drift_silent_when_ok(monkeypatch):
    from harness.tui import app as app_mod

    monkeypatch.setattr(
        "harness.proxy_service.config_gen.config_drift", lambda env=None: "ok"
    )
    stub = _DriftStub()

    app_mod.HarnessTui._check_proxy_config_drift(stub)
    assert stub.logged == []


def test_check_proxy_config_drift_never_raises(monkeypatch):
    from harness.tui import app as app_mod

    def boom(env=None):
        raise RuntimeError("drift check exploded")

    monkeypatch.setattr("harness.proxy_service.config_gen.config_drift", boom)
    stub = _DriftStub()

    app_mod.HarnessTui._check_proxy_config_drift(stub)  # must not raise
    assert any("drift check skipped" in m.lower() for m in stub.logged)


def test_check_proxy_config_drift_ignores_project_local_env_leak(monkeypatch, tmp_path):
    """Reproduces the second-pass review finding: tui_main.py's load_env(cwd)
    merges a project-local .env NEURALWATT_API_KEY into os.environ (override=False)
    BEFORE _check_proxy_config_drift() runs. The drift check must ignore that
    ambient leak entirely and rely only on self._shell_neuralwatt_key (the
    pre-load_env snapshot) — never on os.environ. Simulate the leak via
    monkeypatch.setenv (standing in for load_dotenv's os.environ mutation) while
    leaving the stub's _shell_neuralwatt_key at None (the shell genuinely did not
    export it). If the method regressed to reading os.environ directly (or to
    config_drift()'s os.environ-derived default), it would see the leaked
    project-local key and could report the wrong drift state."""
    from harness.tui import app as app_mod

    # Simulate load_env(cwd)'s leak: a project .env value now sits in os.environ,
    # but the shell never genuinely exported NEURALWATT_API_KEY.
    monkeypatch.setenv("NEURALWATT_API_KEY", "project-local-leaked-key")

    # Isolate machine-global config_dir() so the test doesn't depend on this
    # machine's real ~/.config/harness/.env.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    seen_envs = []

    def fake_config_drift(env=None):
        seen_envs.append(env)
        return "ok"

    monkeypatch.setattr("harness.proxy_service.config_gen.config_drift", fake_config_drift)

    stub = _DriftStub(shell_neuralwatt_key=None)  # shell did NOT export it
    app_mod.HarnessTui._check_proxy_config_drift(stub)

    assert len(seen_envs) == 1
    # The leaked os.environ value must NOT appear in the env config_drift() saw.
    assert seen_envs[0].get("NEURALWATT_API_KEY") != "project-local-leaked-key"
    assert "NEURALWATT_API_KEY" not in seen_envs[0]
    assert stub.logged == []


def test_check_proxy_config_drift_uses_shell_snapshot_when_present(monkeypatch, tmp_path):
    """The inverse case: the shell DID genuinely export NEURALWATT_API_KEY (captured
    pre-load_env as self._shell_neuralwatt_key). That value must reach
    config_drift()'s env, regardless of whatever else os.environ contains."""
    from harness.tui import app as app_mod

    monkeypatch.delenv("NEURALWATT_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    seen_envs = []

    def fake_config_drift(env=None):
        seen_envs.append(env)
        return "ok"

    monkeypatch.setattr("harness.proxy_service.config_gen.config_drift", fake_config_drift)

    stub = _DriftStub(shell_neuralwatt_key="genuine-shell-key")
    app_mod.HarnessTui._check_proxy_config_drift(stub)

    assert len(seen_envs) == 1
    assert seen_envs[0].get("NEURALWATT_API_KEY") == "genuine-shell-key"


def test_check_proxy_config_drift_empty_shell_key_does_not_mask(monkeypatch, tmp_path):
    # Poisoned terminal: pre-launch snapshot captured "" — the overlay must
    # treat it like None (fall through to the file key), so a keyless on-disk
    # config in that terminal reports "drifted", not "ok".
    from harness.tui import app as app_mod
    from harness import paths as harness_paths

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=sk-real-key\n")
    monkeypatch.setattr(harness_paths, "config_dir", lambda: cfg_dir)
    seen_envs = []

    def fake_drift(env=None):
        seen_envs.append(env)
        return "ok"

    monkeypatch.setattr("harness.proxy_service.config_gen.config_drift", fake_drift)
    stub = _DriftStub(shell_neuralwatt_key="")
    app_mod.HarnessTui._check_proxy_config_drift(stub)
    assert seen_envs and seen_envs[0].get("NEURALWATT_API_KEY") == "sk-real-key"


def test_refresh_prompt_decline_falls_back_to_log(monkeypatch):
    from harness.tui import app as app_mod

    logged = []
    pushed = []

    class Stub:
        log = staticmethod(lambda msg: logged.append(msg))
        run_worker = staticmethod(lambda *a, **k: pushed.append(("worker", a)))

        def push_screen(self, modal, callback):
            pushed.append(("screen", type(modal).__name__))
            callback(False)              # user declines

    app_mod.HarnessTui._show_proxy_refresh_prompt(Stub())
    assert ("screen", "ProxyRefreshModal") in pushed
    assert any("proxy config stale" in m for m in logged)
    assert not any(p[0] == "worker" for p in pushed)


def test_refresh_prompt_accept_runs_refresh_worker(monkeypatch):
    from harness.tui import app as app_mod

    workers = []

    class Stub:
        log = staticmethod(lambda msg: None)

        async def _do_proxy_refresh(self):
            pass                          # never actually awaited in this unit test

        def run_worker(self, coro, thread=False):
            coro.close()                 # don't actually run it in this unit test
            workers.append(True)

        def push_screen(self, modal, callback):
            callback(True)               # user accepts

    app_mod.HarnessTui._show_proxy_refresh_prompt(Stub())
    assert workers == [True]
