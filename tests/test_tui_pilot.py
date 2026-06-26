import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace as NS

from acp import update_agent_message_text
from harness.tui.app import HarnessTui, PermissionModal
from harness.tui.messages import SessionUpdate
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
    app.query_one("#landing-input", Input).focus()
    app.query_one("#landing-input", Input).value = text
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
            inp = app.query_one("#landing-input", Input)
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
            y_open = app.query_one("#landing-input", Input).region.y
            assert y_open == y_before, (
                f"input moved when slash menu opened: was row {y_before}, now {y_open}")

            # the menu sits directly above the input (bottom edge on the input's top
            # row) and left-aligns with the compose box — and re-anchors correctly
            # when the row count changes while filtering (no offset race).
            def _check_anchored(label: str) -> None:
                menu = app.query_one("#slash-menu")
                inp_now = app.query_one("#landing-input", Input)
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
            app.query_one("#landing-input", Input).value = "/h"
            for _ in range(20):
                await pilot.pause()
            _check_anchored("filtered")
            app.query_one("#landing-input", Input).value = "/"
            for _ in range(20):
                await pilot.pause()
            _check_anchored("widened back")

            inp = app.query_one("#landing-input", Input)
            # close it again — input returns to the same row
            inp.value = ""
            for _ in range(20):
                await pilot.pause()
                if app._slash is None:
                    break
            y_after = app.query_one("#landing-input", Input).region.y
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
            app.query_one("#landing-input", Input).value = "/"
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
            app.query_one("#landing-input", Input).value = "/"
            for _ in range(20):
                await pilot.pause()
                if app._slash is not None:
                    break
            menu = app.query_one("#slash-menu")
            inp = app.query_one("#landing-input", Input)
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
                    # the modal shows the REAL command (from tool_call.title),
                    # not the opaque tool_call_id
                    assert app.screen._title == "$ echo hello", (
                        f"modal title should be the command, got {app.screen._title!r}")
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
            await app.on_input_submitted(Input.Submitted(inp, "hello while busy"))
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

def test_reload_is_guarded_against_reentry():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True                     # simulate a reload in progress
            gen_before = app._gen
            await app.action_reload()            # must early-return
            assert app._gen == gen_before, "re-entrant reload must be a no-op"
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
