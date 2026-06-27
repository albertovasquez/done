import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace as NS

import acp
from acp import update_agent_message_text, start_tool_call
from harness.tui.app import HarnessTui, PermissionModal
from harness.tui.messages import SessionUpdate
from harness.tui.widgets.prompt_area import PromptArea
from harness.tui.widgets.tool_call_row import ToolCallRow
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static, Input

REPO = Path(__file__).resolve().parent.parent
# Running interpreter (portable across worktrees / any cwd), not a hardcoded
# REPO/.venv path which doesn't exist in a git worktree.
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


def _md_source(md: Markdown) -> str:
    """The accumulated markdown source of a streaming answer widget (the public
    `source` property; falls back to the private store across Textual versions)."""
    return getattr(md, "source", None) or getattr(md, "_markdown", "") or ""


def _transcript_text(app) -> str:
    """The conversation transcript only exists AFTER the first send (the UI starts
    on the centered landing screen). Returns the concatenated text of every
    transcript widget — Static lines (rendered markup) and Markdown sources."""
    try:
        scroll = app.query_one("#transcript", VerticalScroll)
    except Exception:
        return ""
    parts = []
    for w in scroll.children:
        if isinstance(w, Markdown):
            parts.append(_md_source(w))
        elif isinstance(w, Static):
            parts.append(str(w.content))      # the raw markup string
    return "\n".join(parts)


async def _send_first_prompt(pilot, app, text: str) -> None:
    """Type into the landing compose box and submit — this transitions the app
    from the landing state to the conversation state."""
    app.query_one("#landing-input", PromptArea).focus()
    app.query_one("#landing-input", PromptArea).value = text
    await pilot.press("enter")


def test_pilot_starts_on_landing_then_switches_to_conversation():
    """The app boots on the centered landing screen (wordmark + compose, no
    transcript) and switches to the conversation view after the first send."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # landing state: wordmark + landing input present, no transcript yet
            assert app.query("#landing"), "landing container should exist at boot"
            assert app.query("#landing-input"), "landing input should exist at boot"
            assert not app.query("#transcript"), "transcript must NOT exist before first send"
            assert app.theme == "harness", f"harness theme not active: {app.theme}"

            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            # conversation state: landing gone, transcript + bottom composer present
            assert app._started, "did not transition to conversation state"
            assert not app.query("#landing"), "landing should be removed after first send"
            assert app.query("#conversation-input"), "conversation input should exist"

    asyncio.run(go())


def test_pilot_renders_harness_chip_end_to_end():
    """Boot, send a prompt, and assert the harness _meta chip, the agent reply,
    and the per-turn meta line all render in the transcript end-to-end."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()                      # on_mount: spawn+init+session
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "classified: chat_question" in _transcript_text(app):
                    break
            text = _transcript_text(app)
        assert "classified: chat_question" in text, f"harness chip missing.\n{text}"
        assert "hello" in text, f"user message missing.\n{text}"      # rendered as '▌ hello'
        assert "done" in text, f"agent reply missing.\n{text}"
        assert "▣ Build" in text, f"per-turn meta line missing.\n{text}"

    asyncio.run(go())


def test_pilot_streams_deltas_into_one_markdown_widget():
    """Multiple message deltas for one turn accumulate into a SINGLE live Markdown
    widget (not one line per delta), and the 'model is working' indicator appears
    after sending and is gone once the turn completes."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "STREAM please")
            # working indicator should appear while the turn is in flight
            saw_working = False
            for _ in range(50):
                await pilot.pause()
                if app.query("#working"):
                    saw_working = True
                if app._started and app.query("#transcript"):
                    mds = app.query_one("#transcript", VerticalScroll).query(Markdown)
                    if mds and "done" in _md_source(mds.first()):
                        break
            scroll = app.query_one("#transcript", VerticalScroll)
            mds = list(scroll.query(Markdown))
            md_src = _md_source(mds[0]) if mds else ""
            working_after = bool(app.query("#working"))
        assert saw_working, "working indicator never appeared after send"
        assert len(mds) == 1, f"expected ONE markdown widget, got {len(mds)}"
        # all three deltas accumulated into the one widget, in order
        assert md_src == "Hello **world** done", f"deltas not accumulated: {md_src!r}"
        assert not working_after, "working indicator should be gone after the turn"

    asyncio.run(go())


def test_late_prior_turn_delta_does_not_start_block_under_next_prompt():
    """A prompt response may return before the client has processed trailing
    session_update notifications. Starting the next turn must not let a late
    prior-turn delta create a new Markdown block below the next user message."""
    class ControlledConn:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def prompt(self, **kwargs):
            self.started.set()
            await self.release.wait()
            return NS(stop_reason="end_turn")

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            conn = ControlledConn()
            app._conn = conn
            app._session_id = "fake-session"

            app._add_user_message("first")
            app.on_session_update(SessionUpdate(update_agent_message_text("first complete")))
            await pilot.pause()
            app._write_meta(0.1)

            app._add_user_message("second")
            app._turn_start = time.monotonic()
            task = asyncio.create_task(app._send_prompt("second"))
            try:
                await conn.started.wait()
                await pilot.pause()
                assert app.query("#working"), "second turn should still be in flight"

                app.on_session_update(SessionUpdate(update_agent_message_text(" late")))
                await pilot.pause()

                scroll = app.query_one("#transcript", VerticalScroll)
                md_sources = [_md_source(md) for md in scroll.query(Markdown)]
            finally:
                conn.release.set()
                await task

        assert md_sources == ["first complete late"], (
            "late prior-turn delta was rendered as a new answer block under the next prompt: "
            f"{md_sources!r}")

    asyncio.run(go())


def test_pilot_slash_menu_does_not_move_landing_input():
    """Opening the slash menu on the landing screen must not shift the input box.
    The menu should grow upward (overlay) so the input's vertical position is
    pinned — typing '/' and clearing it leaves the input's row unchanged."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            y_before = inp.region.y

            # open the slash menu
            inp.value = "/"
            for _ in range(20):
                await pilot.pause()
                if app._slash is not None:
                    break
            assert app._slash is not None, "slash menu did not open on '/'"
            assert app.query("#slash-menu"), "slash menu widget should be mounted"
            y_open = app.query_one("#landing-input", PromptArea).region.y
            assert y_open == y_before, (
                f"input moved when slash menu opened: was row {y_before}, now {y_open}")

            # the menu sits directly above the input (bottom edge on the input's top
            # row) and left-aligns with the compose box — and re-anchors correctly
            # when the row count changes while filtering (no offset race).
            def _check_anchored(label: str) -> None:
                menu = app.query_one("#slash-menu")
                inp_now = app.query_one("#landing-input", PromptArea)
                compose_x = app.query_one("#landing-compose").region.x
                bottom = menu.region.y + menu.region.height
                assert menu.region.y >= 0, f"{label}: menu clipped off the top"
                assert bottom == inp_now.region.y, (
                    f"{label}: menu bottom {bottom} not pinned to input top "
                    f"{inp_now.region.y}")
                assert menu.region.x == compose_x, (
                    f"{label}: menu x {menu.region.x} not aligned to compose "
                    f"{compose_x}")
                assert inp_now.region.y == y_before, f"{label}: input moved"

            _check_anchored("all commands")

            # filter to fewer rows, then back to all — stays anchored both times
            app.query_one("#landing-input", PromptArea).value = "/h"
            for _ in range(20):
                await pilot.pause()
            _check_anchored("filtered")
            app.query_one("#landing-input", PromptArea).value = "/"
            for _ in range(20):
                await pilot.pause()
            _check_anchored("widened back")

            inp = app.query_one("#landing-input", PromptArea)
            # close it again — input returns to the same row
            inp.value = ""
            for _ in range(20):
                await pilot.pause()
                if app._slash is None:
                    break
            y_after = app.query_one("#landing-input", PromptArea).region.y
            assert y_after == y_before, (
                f"input did not return to its row after closing menu: "
                f"was {y_before}, now {y_after}")

    asyncio.run(go())


def test_pilot_slash_menu_closes_on_resize():
    """A resize moves the centered landing input, which would detach the floating
    menu. The menu is transient, so resizing closes it cleanly (no orphaned
    overlay) and the next keystroke reopens it anchored to the new input row."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).value = "/"
            for _ in range(20):
                await pilot.pause()
                if app._slash is not None:
                    break
            assert app._slash is not None, "menu should be open before resize"

            await pilot.resize_terminal(120, 24)
            for _ in range(20):
                await pilot.pause()
                if app._slash is None:
                    break
            assert app._slash is None, "menu should close on resize"
            assert not app.query("#slash-overlay"), "overlay must not be orphaned"

            # reopen at the new size — bottom re-pins to the input's new row
            app.query_one("#landing-input", PromptArea).value = "/"
            for _ in range(20):
                await pilot.pause()
                if app._slash is not None:
                    break
            menu = app.query_one("#slash-menu")
            inp = app.query_one("#landing-input", PromptArea)
            assert menu.region.y + menu.region.height == inp.region.y, (
                "reopened menu not anchored to the input's new row")

    asyncio.run(go())


def test_session_update_message_carries_session_id():
    from harness.tui.messages import SessionUpdate as SU
    msg = SU("the-update", session_id="sess-7")
    assert msg.update == "the-update"
    assert msg.session_id == "sess-7"

def test_session_update_session_id_defaults_to_none():
    from harness.tui.messages import SessionUpdate as SU
    assert SU("u").session_id is None


def test_pilot_permission_modal_reject():
    """Optional Smoke: fake agent requests permission; rejecting (esc) resolves
    the Future and the turn completes. The permission modal is the shared
    SelectModal-based component — it shows the command as the title and rejects
    on esc (dismiss None)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "please PERMISSION now")
            modal_seen = False
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, PermissionModal):
                    modal_seen = True
                    # title is now the short "Run command?" prompt; the actual
                    # command is in the body slot (_body), not the title.
                    assert app.screen._title == "Run command?", (
                        f"modal title should be 'Run command?', got {app.screen._title!r}")
                    assert app.screen._body == "echo hello", (
                        f"modal body should be the command, got {app.screen._body!r}")
                    await pilot.press("escape")          # esc = reject
                    break
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            text = _transcript_text(app)
        assert modal_seen, "permission modal never appeared"
        assert "done" in text, f"turn did not complete after reject.\n{text}"

    asyncio.run(go())


def test_teardown_then_connect_bumps_generation_and_reconnects():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()                 # on_mount → _connect ran once
            assert app._gen == 1, f"gen should be 1 after startup, got {app._gen}"
            assert app._conn is not None and app._session_id is not None
            await app._teardown()
            assert app._cm is None and app._conn is None and app._session_id is None
            await app._connect()
            assert app._gen == 2, f"gen should bump on reconnect, got {app._gen}"
            assert app._conn is not None and app._session_id is not None
    asyncio.run(go())


def test_teardown_is_idempotent_when_already_torn_down():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._teardown()
            await app._teardown()               # second call must not raise
            assert app._conn is None
    asyncio.run(go())


def test_reset_conversation_empties_transcript_keeps_started():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            assert _transcript_text(app).strip(), "precondition: transcript has content"
            await app._reset_conversation()
            await pilot.pause()
            assert _transcript_text(app) == "", "transcript should be emptied"
            assert app._started is True, "must stay in conversation view, not return to landing"
            assert app.query("#transcript"), "#transcript widget must remain mounted"
            assert app._streaming_md is None and app._stream_buf == ""
            assert app._tokens == 0
    asyncio.run(go())


def test_clear_respawns_agent_and_resets():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            gen_before = app._gen
            await app.action_clear()
            await pilot.pause()
            assert app._gen == gen_before + 1, "clear must now RESPAWN the agent (gen bumps)"
            assert app._conn is not None, "reconnected after respawn"
            assert _transcript_text(app) == "", "conversation reset"
            assert app._busy is False, "busy released"
    asyncio.run(go())


def test_stale_session_update_after_respawn_is_dropped():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            await app.action_clear()            # respawns: bumps _gen; transcript wiped
            await pilot.pause()
            live_session = app._session_id
            # 1) generation filter (load-bearing): an update stamped with the OLD
            #    generation must be dropped even if its session_id matches the live
            #    session.
            stale_gen = SessionUpdate(
                update_agent_message_text("GHOST"),
                session_id=live_session, gen=app._gen - 1)
            app.on_session_update(stale_gen)
            await pilot.pause()
            assert "GHOST" not in _transcript_text(app), \
                "stale-generation update must be dropped"
            # 2) session_id filter (defense-in-depth): an update with the CURRENT
            #    generation but a session_id from a prior session must also drop.
            stale_session = SessionUpdate(
                update_agent_message_text("GHOST"),
                session_id="OLD-SESSION", gen=app._gen)
            app.on_session_update(stale_session)
            await pilot.pause()
            assert "GHOST" not in _transcript_text(app), \
                "stale-session_id update must be dropped"
    asyncio.run(go())


def test_busy_guard_blocks_models_picker_and_prompt_send():
    # Spec §6: while busy (e.g. a reload in flight), /models must not open a
    # picker and a submitted prompt must not start a worker.
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="vibeproxy")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            class _Conn:
                async def prompt(self, **kw):
                    return NS(stop_reason="end_turn")
            app._conn = _Conn(); app._session_id = "fake-session"
            app._busy = True
            # /models is a no-op while busy: no screen pushed, no fetch attempted.
            screens_before = len(app.screen_stack)
            await app.action_select_model()
            await pilot.pause()
            assert len(app.screen_stack) == screens_before, \
                "busy /models must not push a model picker"
            # a prompt submitted while busy must NOT start a worker.
            workers_before = len(app.workers)
            inp = app._active_input()
            inp.value = "hello while busy"
            await app.on_prompt_area_submitted(PromptArea.Submitted(inp, "hello while busy"))
            await pilot.pause()
            assert len(app.workers) == workers_before, \
                "busy prompt-send must not start a worker"
    asyncio.run(go())


def test_send_prompt_finally_no_reenable_after_generation_bump():
    # An old prompt worker whose generation is stale must NOT re-enable input
    # (that would undo a _fatal disable after a reload failure).
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            class _Conn:
                async def prompt(self, **kw):
                    return NS(stop_reason="end_turn")
            app._conn = _Conn(); app._session_id = "fake-session"
            app._send_gen = app._gen
            app._active_input().disabled = True
            app._gen += 1                        # simulate a reload happening mid-flight
            await app._send_prompt("x")          # its captured gen is now stale
            assert app._active_input().disabled is True, "stale worker must not re-enable input"
    asyncio.run(go())


def test_clear_starts_a_new_os_process(tmp_path):
    import os
    marker = tmp_path / "starts.txt"
    cmd = [sys.executable, str(REPO / "tests/fake_agent.py")]
    async def go():
        os.environ["FAKE_AGENT_STARTS_FILE"] = str(marker)
        try:
            app = HarnessTui(agent_cmd=cmd, cwd=str(REPO), model="mock")
            async with app.run_test() as pilot:
                await pilot.pause()
                for _ in range(50):
                    await pilot.pause()
                    if marker.exists() and marker.read_text().count("start") >= 1:
                        break
                starts_before = marker.read_text().count("start")
                await app.action_clear()
                for _ in range(50):
                    await pilot.pause()
                    if marker.read_text().count("start") > starts_before:
                        break
            assert marker.read_text().count("start") == starts_before + 1, (
                "clear must spawn exactly one new agent process")
        finally:
            os.environ.pop("FAKE_AGENT_STARTS_FILE", None)
    asyncio.run(go())

def test_clear_failure_keeps_app_alive_and_input_disabled():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            # make the next _connect fail
            app.agent_cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]
            await app.action_clear()
            await pilot.pause()
            assert app._conn is None, "failed clear leaves no live connection"
            assert app._active_input().disabled is True, "_fatal must disable input"
            assert app._busy is False, "busy released even on failure"
            assert "clear failed" in _transcript_text(app)
    asyncio.run(go())


def test_reset_conversation_resets_snapshot():
    from harness.tui.state import AgentState
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            await app._reset_conversation()
            await pilot.pause()
            assert app._snapshot.active.state == AgentState.IDLE, \
                "snapshot should be reset to IDLE after _reset_conversation"
    asyncio.run(go())

def test_reload_is_guarded_against_reentry():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True                     # simulate a reload in progress
            await app.action_reload()            # must early-return
            assert app._reexec is False, "re-entrant reload must not set _reexec"
            assert app._exit is False, "re-entrant reload must not call exit()"
            app._busy = False
    asyncio.run(go())


def test_reload_sets_reexec_flag_and_exits():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._reexec is False, "starts un-flagged"
            await app.action_reload()
            assert app._reexec is True, "reload must request a re-exec"
            # exit() was requested (Textual sets _exit); the app is on its way down
            assert app._exit is True, "reload must call app.exit()"
    asyncio.run(go())


def test_pilot_escape_clears_input_text():
    """Esc with text in the box (and no slash menu open) clears the box rather
    than cancelling the turn. A second Esc on the now-empty box falls through to
    the global cancel binding (no error)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            inp.value = "some half-typed text"
            await pilot.pause()
            assert app._slash is None, "no slash menu for plain text"

            await pilot.press("escape")
            await pilot.pause()
            assert inp.value == "", "esc should clear the box when it has text"

            # empty box: esc falls through to action_cancel without raising
            await pilot.press("escape")
            await pilot.pause()
            assert inp.value == ""

    asyncio.run(go())


def test_prose_after_tool_opens_new_block():
    """Step-1 prose, then a tool line, then step-2 prose must land in a SEPARATE
    Markdown widget below the tool line — not be appended into step-1's widget."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            app.on_session_update(SessionUpdate(update_agent_message_text("step one")))
            await pilot.pause()
            app.on_session_update(SessionUpdate(start_tool_call(
                tool_call_id="tc1", title="$ ls")))
            await pilot.pause()
            app.on_session_update(SessionUpdate(update_agent_message_text("step two")))
            await pilot.pause()

            scroll = app.query_one("#transcript", VerticalScroll)
            md_sources = [_md_source(md) for md in scroll.query(Markdown)]
        assert md_sources == ["step one", "step two"], (
            f"step-2 prose did not open a new block: {md_sources!r}")

    asyncio.run(go())


def test_pilot_enter_submits_shift_enter_newlines():
    """The compose box (a PromptArea) submits on Enter and inserts a newline on
    Shift+Enter, so a multi-line prompt is sent as one message. Enter must NOT
    leave a stray newline in the box."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            await pilot.press("l", "i", "n", "e", "1")
            await pilot.press("shift+enter")            # newline, stays in the box
            await pilot.press("l", "i", "n", "e", "2")
            await pilot.pause()
            assert inp.value == "line1\nline2", \
                f"shift+enter should insert a newline: {inp.value!r}"
            assert not app._started, "shift+enter must NOT submit"

            await pilot.press("enter")                  # submit the two-line prompt
            for _ in range(50):
                await pilot.pause()
                if app._started and "line1\nline2" in _transcript_text(app):
                    break
            assert app._started, "enter should submit the prompt"
            assert "line1\nline2" in _transcript_text(app), \
                "the full multi-line prompt should reach the transcript"

    asyncio.run(go())


def test_explicit_stream_reset_opens_new_block():
    """A message_chunk carrying _meta stream_reset closes the open block so the
    next delta starts fresh (covers FormatError steps with no tool event)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            app.on_session_update(SessionUpdate(update_agent_message_text("aaa")))
            await pilot.pause()
            reset = update_agent_message_text("")
            # NOTE: Task 4 emits the flag via with_meta(), which nests under
            # field_meta["harness"]. The TUI reader MUST use the nested path.
            reset.field_meta = {"harness": {"stream_reset": True}}
            app.on_session_update(SessionUpdate(reset))
            await pilot.pause()
            app.on_session_update(SessionUpdate(update_agent_message_text("bbb")))
            await pilot.pause()

            scroll = app.query_one("#transcript", VerticalScroll)
            md_sources = [_md_source(md) for md in scroll.query(Markdown)]
        assert md_sources == ["aaa", "bbb"], f"stream_reset did not split blocks: {md_sources!r}"

    asyncio.run(go())


def test_pilot_shift_enter_modifyotherkeys_form_inserts_newline():
    """Some terminals (e.g. cmux/libghostty, Ghostty modifyOtherKeys) send
    Shift+Enter in a form Textual reports as key='shift+\\r' (or 'shift+\\n'),
    NOT 'shift+enter'. The box must still insert a newline and NOT submit for
    these variants — otherwise Shift+Enter silently submits."""
    from textual import events

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            await pilot.press("a")
            for variant in ("shift+\r", "shift+\n"):
                inp.post_message(events.Key(variant, "\r"))
                await pilot.pause()
            await pilot.press("b")
            await pilot.pause()
            assert inp.value == "a\n\nb", \
                f"modifyOtherKeys shift+enter should insert newlines: {inp.value!r}"
            assert not app._started, "shift+enter variants must NOT submit"

    asyncio.run(go())


def test_prompt_area_newline_key_classifier():
    """The structural matcher: any modifier+Enter (Kitty 'shift+enter' form OR
    modifyOtherKeys 'shift+\\r' form, any combo) OR a literal LF (ctrl+j / a key
    carrying '\\n') is a newline; bare Enter (char '\\r') and unrelated keys are
    not. Fast unit test, no Pilot."""
    nl = PromptArea._is_newline_key
    # (key, character) pairs that SHOULD insert a newline
    for k, ch in [("shift+enter", None), ("alt+enter", None), ("ctrl+enter", None),
                  ("alt+shift+enter", None), ("ctrl+shift+enter", None),
                  ("super+enter", None), ("shift+return", None),
                  ("shift+\r", "\r"), ("ctrl+\r", "\r"), ("ctrl+shift+\r", "\r"),
                  ("shift+\n", "\n"), ("ctrl+j", "\n")]:
        assert nl(k, ch), f"({k!r}, {ch!r}) should be a newline"
    # bare Enter (char '\r') submits; these must NOT be newlines
    for k, ch in [("enter", "\r"), ("a", "a"), ("shift+a", "A"),
                  ("ctrl+c", None), ("escape", None), ("tab", None),
                  ("shift+tab", None)]:
        assert not nl(k, ch), f"({k!r}, {ch!r}) must NOT be a newline"


def test_pilot_modified_enter_encodings_insert_newline():
    """Drive the actual widget with the key strings Textual emits for Shift+Enter
    across terminal encodings (Kitty 'shift+enter' + modifyOtherKeys 'shift+\\r'/
    'shift+\\n' + a ctrl variant + ctrl+j). Each must insert a newline, not submit."""
    from textual import events

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            await pilot.press("a")
            variants = [("shift+enter", None), ("shift+\r", "\r"),
                        ("shift+\n", "\n"), ("ctrl+\r", "\r"), ("ctrl+j", "\n")]
            for key, ch in variants:
                inp.post_message(events.Key(key, ch))
                await pilot.pause()
            await pilot.press("b")
            await pilot.pause()
            assert inp.value == "a" + "\n" * len(variants) + "b", \
                f"all soft-return encodings should insert newlines: {inp.value!r}"
            assert not app._started, "soft-return variants must NOT submit"

    asyncio.run(go())


def test_pilot_compose_box_grows_then_caps_at_three_rows():
    """The compose box starts one row tall, grows as lines are added, and is
    capped at three rows (max-height: 3 in app.tcss); a fourth line scrolls
    rather than growing the box further."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            await pilot.pause()
            assert inp.size.height == 1, f"empty box should be 1 row, got {inp.size.height}"

            inp.value = "a\nb"                           # two lines → two rows
            await pilot.pause()
            assert inp.size.height == 2, f"two lines should be 2 rows, got {inp.size.height}"

            inp.value = "a\nb\nc\nd\ne"                   # five lines → capped at 3
            await pilot.pause()
            assert inp.size.height == 3, \
                f"box must cap at 3 rows (max-height), got {inp.size.height}"

    asyncio.run(go())


def test_pilot_snapshot_tracks_turn_lifecycle():
    """After sending a prompt, the app's snapshot leaves IDLE; after the turn
    completes it reaches a terminal state. Proves on_session_update routes
    through the reducer."""
    from harness.tui.state import AgentState
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._snapshot.active.state == AgentState.IDLE, (
                f"snapshot should start IDLE, got {app._snapshot.active.state}")
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            assert app._snapshot.active.state in (AgentState.DONE, AgentState.RESPONDING), \
                f"snapshot did not advance: {app._snapshot.active.state}"
    asyncio.run(go())


def test_pilot_tool_call_is_not_in_transcript_but_in_region():
    """A ToolCallStart update does NOT mount a ToolCallRow in the transcript; the
    pinned ActivityRegion reflects the tool instead."""
    from harness.tui.widgets.activity_region import ActivityRegion
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            tool_start = acp.start_tool_call("tc-test", title="$ echo hello",
                                             status="in_progress")
            app.on_session_update(SessionUpdate(tool_start))
            await pilot.pause()
            scroll = app.query_one("#transcript", VerticalScroll)
            assert not [w for w in scroll.children if isinstance(w, ToolCallRow)], \
                "tool calls must NOT be inline in the transcript"
            # the pinned region tracks the tool in its snapshot
            assert any(tv.id == "tc-test" for tv in app._snapshot.active.tools), \
                "the region's snapshot should track the tool"
            assert app.query_one("#activity-region", ActivityRegion).display is True, \
                "region should be visible while a tool runs"
    asyncio.run(go())


def test_pilot_permission_modal_shows_command_in_body():
    """The permission modal exposes the command in its body (not crammed into
    the title). Title is 'Run command?'; body is the command text."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "please PERMISSION now")
            modal_seen = False
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, PermissionModal):
                    modal_seen = True
                    assert app.screen._title == "Run command?", (
                        f"expected title 'Run command?', got {app.screen._title!r}")
                    assert "echo hello" in app.screen._body, (
                        f"command not in body: {app.screen._body!r}")
                    # The body should be a plain non-markup string (command, no "$ ")
                    assert not app.screen._body.startswith("$ "), (
                        f"body should not start with '$ ', got {app.screen._body!r}")
                    await pilot.press("escape")
                    break
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
        assert modal_seen, "permission modal never appeared"
    asyncio.run(go())


def test_yolo_chip_click_toggles_state():
    """Clicking the footer mode line flips the live bypass state and its text."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock", yolo=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            chip = app.query_one("#statusbar-mode", Static)
            assert "bypass permissions off" in chip._Static__content   # starts off
            app.action_toggle_yolo()
            await pilot.pause(); await pilot.pause()
            assert app._yolo is True
            assert "bypass permissions on" in app.query_one("#statusbar-mode", Static)._Static__content

    asyncio.run(go())


def test_compose_meta_shows_bypass_marker_when_on():
    """The top mode line ('Build · …') gains a RED 'bypass on' marker when the
    live gate is on, so the posture is visible at the top as well as the footer."""
    app = HarnessTui.__new__(HarnessTui)        # bypass Textual mount; pure method
    app.model = "mock"
    app._yolo = False
    assert "bypass" not in app._compose_meta_markup("mock model", "Mock")
    app._yolo = True
    markup = app._compose_meta_markup("mock model", "Mock")
    assert "bypass on" in markup and "$error" in markup   # red, present


def test_yolo_chip_is_leftmost_in_statusbar():
    """The mode chip mounts FIRST (left edge), not buried behind the 1fr cwd."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock", yolo=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            ids = [w.id for w in app.query_one("#statusbar").children]
            assert ids[0] == "statusbar-mode", f"chip not leftmost: {ids}"
    asyncio.run(go())


def test_status_bar_shows_persona_after_chip():
    """A session/update whose field_meta carries the persona chip causes the
    #statusbar-persona Static to show the persona id. Before the chip lands the
    widget renders an empty string (hidden)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # persona chip must be invisible before any PersonaResolved arrives
            persona_widget = app.query_one("#statusbar-persona", Static)
            assert persona_widget._Static__content == "", (
                f"persona chip should be empty before first chip, got "
                f"{persona_widget._Static__content!r}")

            # enter conversation so on_session_update is not dropped
            await app._enter_conversation()
            app._session_id = "fake-session"

            # deliver a session/update carrying the persona chip
            update = update_agent_message_text("")
            update.field_meta = {"harness": {"persona": {"id": "fred"}}}
            app.on_session_update(SessionUpdate(update))
            await pilot.pause()

            persona_widget = app.query_one("#statusbar-persona", Static)
            assert "fred" in persona_widget._Static__content, (
                f"persona chip should show 'fred', got "
                f"{persona_widget._Static__content!r}")

    asyncio.run(go())


def test_statusbar_children_share_one_row():
    """Regression: #statusbar is a horizontal layout, so chip + cwd + version sit
    on the SAME row. A vertical Container default stacked them onto 3 rows and the
    height:1 bar clipped all but the first (the chip), hiding the cwd/version and
    making the bar look broken. Assert on region.y (NOT size.width — width is set
    per-widget regardless of which row it lands on, so it can't catch this)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock", yolo=False)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            kids = {w.id: w.region for w in app.query_one("#statusbar").children}
            ys = {wid: r.y for wid, r in kids.items()}
            assert len(set(ys.values())) == 1, f"statusbar children not on one row: {ys}"
            # left-to-right order: mode chip, then cwd, then version
            assert kids["statusbar-mode"].x < kids["statusbar-left"].x < kids["statusbar-right"].x
    asyncio.run(go())


def test_agent_rail_renders_rows_and_posts_selection():
    from harness.tui.widgets.agent_rail import AgentRail, PersonaSelected
    from harness.tui.roster import PersonaRow
    from textual.app import App

    posted = []

    class _Probe(App):
        def compose(self):
            yield AgentRail(id="rail")
        def on_persona_selected(self, msg: PersonaSelected):
            posted.append(msg.id)

    async def go():
        app = _Probe()
        async with app.run_test() as pilot:
            rail = app.query_one("#rail", AgentRail)
            rail.set_rows((
                PersonaRow(id="default", name="default", active=False),
                PersonaRow(id="fred", name="Fred R.", active=True),
            ))
            await pilot.pause()
            # the rendered content shows both names with correct glyphs
            text = rail._rail_text()
            assert "default" in text and "Fred R." in text
            assert "● Fred R." in text, f"active glyph missing: {text!r}"
            assert "○ default" in text, f"idle glyph missing: {text!r}"
            # selecting the "fred" row posts PersonaSelected("fred")
            rail.select_id("fred")             # a direct selection entrypoint the widget exposes
            await pilot.pause()
            assert posted == ["fred"]

    asyncio.run(go())


def test_agent_rail_listview_selected_event_path():
    """Cover the @on(ListView.Selected) → item.data → PersonaSelected round-trip.

    This is the path exercised on real keyboard/click selection, distinct from
    the programmatic select_id() helper tested above."""
    from harness.tui.widgets.agent_rail import AgentRail, PersonaSelected
    from harness.tui.roster import PersonaRow
    from textual.app import App
    from textual.widgets import ListView, ListItem

    posted = []

    class _Probe(App):
        def compose(self):
            yield AgentRail(id="rail")
        def on_persona_selected(self, msg: PersonaSelected):
            posted.append(msg.id)

    async def go():
        app = _Probe()
        async with app.run_test() as pilot:
            rail = app.query_one("#rail", AgentRail)
            rail.set_rows((
                PersonaRow(id="default", name="default", active=False),
                PersonaRow(id="fred", name="Fred R.", active=True),
            ))
            await pilot.pause()
            # Find the ListItem for "default" that set_rows() created (has .data = "default")
            items = list(rail.query(ListItem))
            default_item = next(i for i in items if getattr(i, "data", None) == "default")
            # Fire the real ListView.Selected event directly — this is the path
            # _on_selected() handles; proves item.data → PersonaSelected("default").
            rail.post_message(ListView.Selected(rail, default_item, 0))
            await pilot.pause()
            assert "default" in posted, (
                f"PersonaSelected not posted via ListView.Selected path; got: {posted}"
            )

    asyncio.run(go())


# ---- Task 4: rail mount, tab toggle, persona selection wiring ----

def test_rail_hidden_by_default_and_tab_toggles():
    """Rail starts hidden; Tab from the prompt reveals it (focus-traversal model).
    Esc from the rail hides it and returns focus to the prompt."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import AgentRail
            from harness.tui.widgets.prompt_area import PromptArea
            rail = app.query_one("#agent-rail", AgentRail)
            assert rail.display is False       # hidden by default

            # Tab from the prompt (landing-input has focus on startup) → reveals rail
            prompt = app.query_one("#landing-input", PromptArea)
            prompt.focus()
            await pilot.pause()
            assert isinstance(app.focused, PromptArea), "prompt should be focused"
            await pilot.press("tab")
            await pilot.pause()
            assert rail.display is True        # rail revealed
            assert isinstance(app.focused, AgentRail), "rail should have focus"

            # Esc from the rail → hides rail and returns focus to prompt
            await pilot.press("escape")
            await pilot.pause()
            assert rail.display is False       # rail hidden
            assert isinstance(app.focused, PromptArea), "focus back to prompt"
    asyncio.run(go())


def test_tab_from_prompt_reveals_rail_and_focuses_it():
    """Tab pressed while prompt is focused and rail is hidden: rail opens and
    gets focus. Second path: action_toggle_rail still works as a direct caller."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import AgentRail
            from harness.tui.widgets.prompt_area import PromptArea
            rail = app.query_one("#agent-rail", AgentRail)
            assert rail.display is False

            # Focus prompt, press Tab → rail should open and be focused
            app.query_one("#landing-input", PromptArea).focus()
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert rail.display is True, "rail must be displayed after tab from prompt"
            assert isinstance(app.focused, AgentRail), "AgentRail must hold focus"

            # action_toggle_rail still closes it (used by /persona no-arg)
            app.action_toggle_rail()
            await pilot.pause()
            assert rail.display is False
    asyncio.run(go())


def test_esc_from_rail_hides_and_refocuses_prompt():
    """Esc while the rail is focused hides the rail and returns focus to the prompt."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import AgentRail
            from harness.tui.widgets.prompt_area import PromptArea
            # Open the rail via action (direct path, no key interception needed)
            app.action_toggle_rail()
            await pilot.pause()
            rail = app.query_one("#agent-rail", AgentRail)
            assert rail.display is True
            # Rail should have focus (action_toggle_rail calls rail.focus())
            assert isinstance(app.focused, AgentRail), "rail must be focused after open"

            # Press Esc → hide rail, return focus to prompt
            await pilot.press("escape")
            await pilot.pause()
            assert rail.display is False, "rail must hide on Esc"
            assert isinstance(app.focused, PromptArea), "focus must return to prompt"
    asyncio.run(go())


def test_selecting_persona_sets_switch_and_reexec():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import PersonaSelected
            # simulate selecting a non-active persona
            app.post_message(PersonaSelected("fred"))
            await pilot.pause()
            assert app._switch_persona == "fred"
            assert app._reexec is True
    asyncio.run(go())


def test_selecting_active_persona_is_noop():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import PersonaSelected
            active = app._snapshot.active_id
            app.post_message(PersonaSelected(active))
            await pilot.pause()
            assert app._switch_persona is None     # no switch
            assert app._reexec is False            # no re-exec
    asyncio.run(go())


# ---- C2b Bug 3: no-op guard uses stale snapshot active_id before first turn ----

def test_selecting_launch_persona_before_first_turn_is_noop():
    """App launched with --persona fred: selecting 'fred' before the first
    PersonaResolved chip lands must be a no-op (already on fred). Before this
    fix, _snapshot.active_id = 'default' (initial_snapshot) so selecting 'fred'
    wrongly triggered a re-exec."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock",
                         persona="fred")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import PersonaSelected
            assert app._persona_seen is False, "no chip should have landed yet"
            app.post_message(PersonaSelected("fred"))
            await pilot.pause()
            assert app._switch_persona is None, (
                "selecting the launch persona before first chip must be a no-op")
            assert app._reexec is False
    asyncio.run(go())


def test_selecting_different_persona_before_first_turn_switches():
    """App launched with --persona fred: selecting 'default' before the first
    chip lands must trigger a switch (they're different personas)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock",
                         persona="fred")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import PersonaSelected
            assert app._persona_seen is False, "no chip should have landed yet"
            app.post_message(PersonaSelected("default"))
            await pilot.pause()
            assert app._switch_persona == "default", (
                "selecting a different persona before first chip must switch")
            assert app._reexec is True
    asyncio.run(go())


# ---- C2b Bug 4: mid-turn switch must be refused ----

def test_switch_refused_while_turn_active():
    """Selecting a persona while a turn is in flight must be refused (no re-exec,
    no switch set). The turn-active flag must be checked."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import PersonaSelected
            # Simulate a turn in flight by setting the flag directly
            app._turn_active = True
            app.post_message(PersonaSelected("fred"))
            await pilot.pause()
            assert app._switch_persona is None, (
                "switch must be refused while a turn is active")
            assert app._reexec is False
    asyncio.run(go())


def test_switch_allowed_when_no_turn_active():
    """Selecting a persona when no turn is in flight must proceed normally."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import PersonaSelected
            assert app._turn_active is False, "no turn running at boot"
            app.post_message(PersonaSelected("fred"))
            await pilot.pause()
            assert app._switch_persona == "fred"
            assert app._reexec is True
    asyncio.run(go())


# ---- C2b Bug 5: tab must not intercept PromptArea focus traversal ----

def test_tab_from_prompt_opens_rail_focus_traversal_model():
    """Focus-traversal model (C2b): Tab from the prompt reveals the agent rail
    and moves focus to it. The app's on_key intercepts Tab only when the prompt
    has focus and the rail is hidden — so Tab literally navigates 'to the agents'."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            from harness.tui.widgets.agent_rail import AgentRail
            # PromptArea has focus at boot (on_mount focuses it)
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            await pilot.pause()
            rail = app.query_one("#agent-rail", AgentRail)
            assert rail.display is False, "rail starts hidden"
            # Tab while prompt has focus: app.on_key intercepts → reveal+focus rail
            await pilot.press("tab")
            await pilot.pause()
            assert rail.display is True, (
                "tab with prompt focused must reveal the rail (focus-traversal model)")
            assert isinstance(app.focused, AgentRail), "focus must move to the rail"
    asyncio.run(go())


def test_tab_binding_removed_no_global_toggle():
    """Structural check: the Tab binding is no longer in HarnessTui.BINDINGS.
    Tab is now handled by on_key (focus-aware interception), not a global binding."""
    from textual.binding import Binding
    tab_bindings = [b for b in HarnessTui.BINDINGS
                    if isinstance(b, Binding) and b.key == "tab"]
    assert not tab_bindings, (
        "Tab must NOT be in BINDINGS — it is handled by on_key for focus-traversal"
    )
