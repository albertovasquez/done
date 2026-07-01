import sys

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import acp
from acp import update_agent_message_text, start_tool_call
from harness.tui.app import HarnessTui, PermissionModal
from harness.tui.messages import SessionUpdate
from harness.tui.widgets.prompt_area import PromptArea
from harness.tui.widgets.tool_call_row import ToolCallRow
from textual import events
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static, Input

REPO = Path(__file__).resolve().parent.parent
# Running interpreter (portable across worktrees / any cwd), not a hardcoded
# REPO/.venv path which doesn't exist in a git worktree.
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

# The per-turn run-caption footer marker. The mode word was replaced by the
# active persona's display name; in mock mode with no --persona that resolves to
# the default persona, whose shipped name is "Bob" (seeded into an isolated
# config dir by the autouse fixture below, so this is deterministic on any box).
RUN_CAPTION = "▣ Bob"   # the shipped default persona's display name


@pytest.fixture(autouse=True)
def _isolated_default_persona(monkeypatch, tmp_path_factory):
    """Point XDG_CONFIG_HOME at a fresh dir and seed the default persona, so the
    footer caption resolves to the shipped "Bob" name regardless of the
    developer's (or CI's) real ~/.config. Without this, the caption depends on
    machine state — present-and-named on a dev box, falling back to the id
    "default" on a clean checkout."""
    cfg = tmp_path_factory.mktemp("xdg_config")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    from harness import persona
    persona.seed_default_workspace()


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
        assert RUN_CAPTION in text, f"per-turn meta line missing.\n{text}"

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


def test_pilot_hides_sentinel_typed_into_prose():
    """Real render path: when a model TYPES `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`
    into its answer prose (rather than running it as a tool), the rendered Markdown
    widget must not show it. The tool-row guard never sees a typed line, so the
    strip happens at flush time — verify through the actual streaming path."""
    sentinel = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"
            # streamed in chunks, with the sentinel split across two deltas as the
            # real agent stream can deliver it
            app._stream_message("Here is the answer.\n\n")
            app._stream_message("echo COMPLETE_TASK_AND_")
            await pilot.pause()
            app._stream_message("SUBMIT_FINAL_OUTPUT")
            await pilot.pause()
            await pilot.pause()
            scroll = app.query_one("#transcript", VerticalScroll)
            mds = list(scroll.query(Markdown))
            src = _md_source(mds[0]) if mds else ""
        assert len(mds) == 1, f"expected ONE markdown widget, got {len(mds)}"
        assert sentinel not in src, f"sentinel leaked into prose: {src!r}"
        assert "Here is the answer." in src, f"real answer was dropped: {src!r}"

    asyncio.run(go())


def _footer(app):
    """The most recent turn footer Static (class 'turn-meta-run')."""
    scroll = app.query_one("#transcript", VerticalScroll)
    foots = [w for w in scroll.children
             if isinstance(w, Static) and "turn-meta-run" in (w.classes or set())]
    return foots[-1] if foots else None


def test_footer_carries_copy_affordance():
    """The turn footer shows a clickable (copy) affordance (the response text is
    resolved at click time, not stashed here)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"
            app._stream_message("Hello **world** done")
            await pilot.pause()
            app._write_meta(1.2)
            await pilot.pause()
            foot = _footer(app)
            assert foot is not None, "no turn-meta-run footer was appended"
            assert "(copy)" in str(foot.content), "footer is missing the copy affordance"
            assert getattr(foot, "_copyable", False) is True

    asyncio.run(go())


def test_footer_click_copies_response_to_clipboard():
    """Clicking the footer copies THIS turn's response and flips to (copied).
    Reproduces the real late-drain order: the footer is appended at turn end
    (prompt() returns) and the response Markdown drains AFTER. The copy must
    still resolve the response — it reads the Markdown live, not a build-time
    buffer snapshot (regression guard for the empty-copy bug)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        copied = []
        # Patch the app's own clipboard seam so the test never touches the real OS
        # clipboard or runs pbcopy; returns True ⇒ the label should flip.
        app._copy_to_clipboard = lambda text: (copied.append(text) or True)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"
            app._write_meta(0.5)                 # footer FIRST (turn end)…
            await pilot.pause()
            app._stream_message("the answer")    # …response drains AFTER (late delivery)
            await pilot.pause()
            foot = _footer(app)
            app.on_click(NS(widget=foot))        # simulate a click on the footer
            await pilot.pause()
            assert copied == ["the answer"], f"clipboard got {copied!r}"
            assert "(copied)" in str(foot.content), "label did not flip to the copied state"

    asyncio.run(go())


def test_footer_sits_below_a_late_draining_response():
    """The run-caption footer must render BELOW the answer even when the response
    drains AFTER the footer is appended (prompt() returns before the trailing
    message deltas arrive — see _stream_message late-delivery). Reproduces the
    real failure where the footer mounted first and the late answer landed under
    it, so '▣ … (copy)' showed ABOVE the prose. The fix re-parents the footer
    below the response when the late delta lands."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"
            app._write_meta(0.5)                 # footer FIRST (turn end)…
            await pilot.pause()
            app._stream_message("the late answer")  # …response drains AFTER
            await pilot.pause()
            scroll = app.query_one("#transcript", VerticalScroll)
            kids = list(scroll.children)
            foot = _footer(app)
            md = next(w for w in kids if isinstance(w, Markdown))
            assert kids.index(md) < kids.index(foot), (
                "footer rendered ABOVE the late-draining response — "
                f"order: {[type(w).__name__ for w in kids]}")

    asyncio.run(go())


def test_footer_copy_is_noop_when_no_response_rendered():
    """A footer with no answer above it (e.g. a tool-only turn) copies nothing and
    does not flip — no crash, no empty clipboard write."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        copied = []
        app._copy_to_clipboard = lambda text: (copied.append(text) or True)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"
            app._write_meta(0.3)                 # footer with no response Markdown anywhere
            await pilot.pause()
            foot = _footer(app)
            app.on_click(NS(widget=foot))
            await pilot.pause()
            assert copied == [], f"copied despite no response: {copied!r}"
            assert "(copy)" in str(foot.content) and "(copied)" not in str(foot.content)

    asyncio.run(go())


def test_footer_copy_native_first_then_flips(monkeypatch):
    """The real _copy_to_clipboard path: when a native tool succeeds, OSC 52 is NOT
    used and the label flips to (copied)."""
    import harness.tui.clipboard as clip
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        native_calls, osc_calls = [], []
        monkeypatch.setattr(clip, "native_copy", lambda t, **k: (native_calls.append(t) or True))
        app.copy_to_clipboard = lambda t: osc_calls.append(t)   # OSC 52 should NOT fire
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"
            app._stream_message("answer text")
            await pilot.pause()
            app._write_meta(0.4)
            await pilot.pause()
            foot = _footer(app)
            app.on_click(NS(widget=foot))
            await pilot.pause()
            assert native_calls == ["answer text"], native_calls
            assert osc_calls == [], "OSC 52 used despite native success"
            assert "(copied)" in str(foot.content)

    asyncio.run(go())


def test_footer_copy_falls_back_to_osc52_when_no_native_tool(monkeypatch):
    """When no native tool is present, fall back to OSC 52 (Textual) and still
    flip to (copied)."""
    import harness.tui.clipboard as clip
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        osc_calls = []
        monkeypatch.setattr(clip, "native_copy", lambda t, **k: False)  # no native tool
        app.copy_to_clipboard = lambda t: osc_calls.append(t)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"
            app._stream_message("via osc52")
            await pilot.pause()
            app._write_meta(0.4)
            await pilot.pause()
            foot = _footer(app)
            app.on_click(NS(widget=foot))
            await pilot.pause()
            assert osc_calls == ["via osc52"], osc_calls
            assert "(copied)" in str(foot.content)

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


def test_new_turn_prose_opens_fresh_block_after_classify_chip():
    """Regression: from turn 2 on, the agent emits a task_classified chip (empty
    body) BEFORE the turn's prose. By then the prior answer's kept Markdown widget
    is no longer last (footer + new prompt + chip mounted after it) and
    _add_user_message has cleared _boundary_after — so without treating the chip as
    a turn boundary, the late-delta branch in _stream_message appends turn 2's
    prose INTO turn 1's widget (answer renders under the wrong prompt). The chip
    must open a fresh block for turn 2's answer."""
    from harness.acp_emit import with_meta, message_chunk

    def _classify_chip(task_type="chat_question"):
        meta = {"task_type": task_type, "skills": [], "confidence": 0.99}
        return with_meta(message_chunk(""), {"task_classified": meta})

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            # turn 1: prompt, prose, footer
            app._add_user_message("first")
            app.on_session_update(SessionUpdate(update_agent_message_text("first answer")))
            await pilot.pause()
            app._write_meta(0.1)

            # turn 2: new prompt, then the classify chip (empty body), then prose
            app._add_user_message("second")
            app.on_session_update(SessionUpdate(_classify_chip()))
            await pilot.pause()
            app.on_session_update(SessionUpdate(update_agent_message_text("second answer")))
            await pilot.pause()

            scroll = app.query_one("#transcript", VerticalScroll)
            md_sources = [_md_source(md) for md in scroll.query(Markdown)]
        assert md_sources == ["first answer", "second answer"], (
            "turn-2 prose was merged into turn-1's widget instead of opening a "
            f"fresh block: {md_sources!r}")

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


def test_teardown_swallows_acp_queue_closed_shutdown_race():
    """acp.Connection.close() closes its message queue before cancelling the
    receive-loop task, so a message already in flight can lose that race and
    raise RuntimeError("mssage queue already closed") out of __aexit__. This is
    a known shutdown-ordering bug in the acp library (not ours to fix) — verify
    _teardown treats it as benign rather than letting it crash the app."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()

            class _RacingCm:
                async def __aexit__(self, *exc):
                    raise RuntimeError("mssage queue already closed")

            app._cm = _RacingCm()
            await app._teardown()                # must not raise
            assert app._cm is None and app._conn is None
    asyncio.run(go())


def test_teardown_reraises_unrelated_runtime_errors():
    """The acp shutdown-race guard must stay narrow — any other RuntimeError
    from __aexit__ is a real failure and must still propagate."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()

            class _BrokenCm:
                async def __aexit__(self, *exc):
                    raise RuntimeError("subprocess refused to terminate")

            app._cm = _BrokenCm()
            with pytest.raises(RuntimeError, match="subprocess refused to terminate"):
                await app._teardown()
    asyncio.run(go())


def _mouse_down() -> "events.MouseDown":
    from textual.geometry import Offset
    return events.MouseDown(
        widget=None, x=1, y=1, delta_x=0, delta_y=0,
        button=1, shift=False, meta=False, ctrl=False,
        screen_x=1, screen_y=1,
    )


def test_on_event_drops_mouse_event_during_stream_remount_race():
    """The StreamPainter flush remounts the answer widget's children at 12Hz
    while streaming (Markdown.update → remove+mount_all). A mouse event landing in
    that window can resolve to a since-removed child, and Textual's own
    Screen._forward_event dereferences `container.region` unguarded, raising
    AttributeError. Verify on_event treats that as a missed click instead of a
    fatal app crash."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.app import App

            async def _boom(self, event):
                raise AttributeError("'NoneType' object has no attribute 'region'")
            orig = App.on_event
            App.on_event = _boom
            try:
                await app.on_event(_mouse_down())   # must not raise
            finally:
                App.on_event = orig
    asyncio.run(go())


def test_on_event_reraises_attribute_errors_unrelated_to_mouse_region():
    """The remount-race guard must stay narrow: an AttributeError from a non-
    mouse event, or one that isn't about `region`, is a real bug and must
    still propagate (and crash loudly, same as before this fix)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.app import App

            async def _boom(self, event):
                raise AttributeError("'NoneType' object has no attribute 'frobnicate'")
            orig = App.on_event
            App.on_event = _boom
            try:
                with pytest.raises(AttributeError, match="frobnicate"):
                    await app.on_event(_mouse_down())
            finally:
                App.on_event = orig
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


# ---- mid-turn: input stays usable, Enter queues, queue drains on turn end ----

class _GatedConn:
    """A fake ACP connection whose prompt() blocks until `release` is set, so a
    turn can be held 'in flight' while the test pokes the UI. Records each prompt."""
    def __init__(self):
        self.release = asyncio.Event()
        self.prompts = []
        self.cancels = 0

    async def prompt(self, *, prompt, session_id, **kw):
        self.prompts.append("".join(getattr(b, "text", "") for b in prompt))
        await self.release.wait()
        return NS(stop_reason="end_turn")

    async def cancel(self, **kw):
        self.cancels += 1


async def _start_turn(pilot, app, text):
    """Mount conversation, wire a gated conn, and start a turn that hangs."""
    await app._enter_conversation()
    await pilot.pause()                     # let #transcript/#composer mount
    conn = _GatedConn()
    app._conn = conn
    app._session_id = "fake-session"
    inp = app._active_input()
    inp.value = text
    await app.on_prompt_area_submitted(PromptArea.Submitted(inp, text))
    await pilot.pause()
    return conn


async def _drain(pilot, app, conn):
    """Release the gated turn and let all workers finish before teardown, so the
    _send_prompt finally doesn't query a half-dismantled screen at exit."""
    conn.release.set()
    for _ in range(50):
        await pilot.pause()
        if not app._turn_active and not app._queued:
            break


def test_input_usable_while_turn_active():
    """The composer must NOT be disabled during a turn — the user can click in and
    type their next message while the agent works."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            conn = await _start_turn(pilot, app, "first")
            try:
                assert app._turn_active is True, "turn should be in flight"
                assert app._active_input().disabled is False, \
                    "input must stay enabled during a turn so the user can type"
            finally:
                await _drain(pilot, app, conn)   # never leave a hung worker for teardown
    asyncio.run(go())


def test_enter_during_turn_queues_and_does_not_start_second_turn():
    """Pressing Enter mid-turn must enqueue the text (not start a 2nd concurrent
    prompt). Only one prompt() call until the first turn ends."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            conn = await _start_turn(pilot, app, "first")
            try:
                assert conn.prompts == ["first"], "only the first prompt is in flight"

                # type + submit a second message while the turn is active
                inp = app._active_input()
                inp.value = "second"
                await app.on_prompt_area_submitted(PromptArea.Submitted(inp, "second"))
                await pilot.pause()

                assert "second" in app._queued, "mid-turn Enter must enqueue the message"
                assert conn.prompts == ["first"], "a second turn must NOT start concurrently"
                assert app._active_input().value == "", "the box is cleared after queueing"

                await _drain(pilot, app, conn)     # let the first turn finish → drain queue
                assert conn.prompts == ["first", "second"], \
                    "the queued message must auto-send when the turn ends"
                assert app._queued == [], "queue is drained"
            finally:
                await _drain(pilot, app, conn)
    asyncio.run(go())


def test_tab_opens_rail_during_turn():
    """Tab must open the persona rail even while a turn is active (the input is no
    longer disabled, so it keeps focus and Tab reveals the rail)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            conn = await _start_turn(pilot, app, "first")
            try:
                app._active_input().focus()
                await pilot.pause()
                assert app.query_one("#agent-drawer").display is False
                await pilot.press("tab")
                await pilot.pause()
                assert app.query_one("#agent-drawer").display is True, \
                    "Tab must open the persona rail during a turn"
            finally:
                await _drain(pilot, app, conn)
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


def test_status_right_shows_context_used_and_remaining():
    app = HarnessTui.__new__(HarnessTui)
    app.model = "vibeproxy"
    app._worker_model_id = "claude-opus-4-8"
    app._started = True
    app._tokens = 12_345

    markup = app._status_right()

    assert "ctx 12.3K/1.0M" in markup
    assert "| 987.7K left" in markup


def test_status_right_shows_context_window_before_usage():
    app = HarnessTui.__new__(HarnessTui)
    app.model = "vibeproxy"
    app._worker_model_id = "gpt-5.4"
    app._started = True
    app._tokens = 0

    assert "ctx --/400.0K" in app._status_right()


def test_compose_meta_shows_persona_name():
    """The compose-meta line under the input is the active persona's display name
    (bare, no parens) — it replaced the old 'Build' mode word. model/provider
    moved up under the header rule, bypass shows in the footer chip, so neither
    appears here regardless of the live gate."""
    app = HarnessTui.__new__(HarnessTui)        # bypass Textual mount; pure method
    app.model = "mock"
    app._mode_label = lambda: "bob"             # stub the persona-name lookup
    for yolo in (False, True):
        app._yolo = yolo
        markup = app._compose_meta_markup("mock model", "Mock")
        assert "bob" in markup
        assert "Build" not in markup
        assert "(" not in markup and ")" not in markup   # bare, no parens
        assert "bypass" not in markup
        assert "mock model" not in markup and "Mock" not in markup


def test_run_caption_shows_persona_name():
    """The per-turn run caption footer ('▣ <persona> · model · Ns · (copy)') uses
    the active persona name in place of 'Build', kept consistent with the
    composer line."""
    app = HarnessTui.__new__(HarnessTui)
    app.model = "mock"
    app._worker_model_id = None
    app._yolo = False
    app._mode_label = lambda: "josh"
    markup = app._meta_markup(1.2)
    assert "▣ josh" in markup
    assert "Build" not in markup


def test_meta_markup_compaction_note_present():
    """When _compacted is set for the turn, _meta_markup includes the compaction
    note with '→' and 'context compacted'."""
    app = HarnessTui.__new__(HarnessTui)
    app.model = "mock"
    app._worker_model_id = None
    app._yolo = False
    app._mode_label = lambda: "Build"
    # Simulate a compaction event having been recorded for this turn.
    app._compacted = {"before_msgs": 40, "after_msgs": 15}
    markup = app._meta_markup(2.5)
    assert "context compacted" in markup
    assert "→" in markup
    assert "40" in markup
    assert "15" in markup


def test_meta_markup_no_compaction_by_default():
    """When _compacted is None (no compaction this turn), the footer is silent."""
    app = HarnessTui.__new__(HarnessTui)
    app.model = "mock"
    app._worker_model_id = None
    app._yolo = False
    app._mode_label = lambda: "Build"
    app._compacted = None
    markup = app._meta_markup(1.0)
    assert "context compacted" not in markup
    assert "↯" not in markup


def test_landing_placeholder_persona_aware():
    """default persona keeps the original placeholder (with the ›-prefix and the
    example quote); a non-default persona swaps to 'Ask <name> anything…' (no
    example). The › chevron prefixes both."""
    app = HarnessTui.__new__(HarnessTui)
    # default → unchanged text, chevron prefix, example quote retained.
    app._current_persona = lambda: "default"
    app._persona_display_name = lambda pid: pid
    default_ph = app._landing_placeholder()
    assert default_ph.startswith("› ")
    assert "tech stack" in default_ph
    assert "Ask anything" in default_ph
    # non-default → personalized opener, no example quote.
    app._current_persona = lambda: "bob"
    bob_ph = app._landing_placeholder()
    assert bob_ph.startswith("› ")
    assert "Ask bob anything" in bob_ph
    assert "tech stack" not in bob_ph


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
            # the rendered cards show both names; active is accent-bold, idle plain
            text = rail._rail_text()
            assert "default" in text and "Fred R." in text
            assert "[$accent][b]Fred R.[/b][/]" in text, f"active card styling missing: {text!r}"
            assert "[$foreground]default[/]" in text, f"idle card styling missing: {text!r}"
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
            assert app.query_one("#agent-drawer").display is False       # hidden by default

            # Tab from the prompt (landing-input has focus on startup) → reveals rail
            prompt = app.query_one("#landing-input", PromptArea)
            prompt.focus()
            await pilot.pause()
            assert isinstance(app.focused, PromptArea), "prompt should be focused"
            await pilot.press("tab")
            await pilot.pause()
            assert app.query_one("#agent-drawer").display is True        # rail revealed
            assert isinstance(app.focused, AgentRail), "rail should have focus"

            # Esc from the rail → hides rail and returns focus to prompt
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#agent-drawer").display is False       # rail hidden
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
            assert app.query_one("#agent-drawer").display is False

            # Focus prompt, press Tab → rail should open and be focused
            app.query_one("#landing-input", PromptArea).focus()
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert app.query_one("#agent-drawer").display is True, "rail must be displayed after tab from prompt"
            assert isinstance(app.focused, AgentRail), "AgentRail must hold focus"

            # action_toggle_rail still closes it (used by /persona no-arg)
            app.action_toggle_rail()
            await pilot.pause()
            assert app.query_one("#agent-drawer").display is False
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
            assert app.query_one("#agent-drawer").display is True
            # Rail should have focus (action_toggle_rail calls rail.focus())
            assert isinstance(app.focused, AgentRail), "rail must be focused after open"

            # Press Esc → hide rail, return focus to prompt
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#agent-drawer").display is False, "rail must hide on Esc"
            assert isinstance(app.focused, PromptArea), "focus must return to prompt"
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
            assert app.query_one("#agent-drawer").display is False, "rail starts hidden"
            # Tab while prompt has focus: app.on_key intercepts → reveal+focus rail
            await pilot.press("tab")
            await pilot.pause()
            assert app.query_one("#agent-drawer").display is True, (
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


# ---- FIX 3: rail highlight uses _current_persona() not stale snapshot ----

def test_persona_rows_highlights_launch_persona_before_first_turn():
    """_persona_rows must use _current_persona() for the active-id argument.
    Before FIX 3, it used self._snapshot.active_id which is 'default' (initial
    snapshot) even when launched as 'fred', so the fred row was not highlighted."""
    from harness.tui.widgets.agent_rail import AgentRail
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock", persona="fred")
    # Before the first PersonaResolved, _persona_seen is False.
    # _current_persona() must return "fred" (the launch persona).
    assert app._current_persona() == "fred", (
        "_current_persona() must return launch persona before first PersonaResolved"
    )
    # _snapshot.active_id is still "default" (initial_snapshot)
    assert app._snapshot.active_id != "fred", (
        "snapshot active_id is stale 'default' before first PersonaResolved"
    )
    # _persona_rows must use _current_persona(), not snapshot.active_id
    # We test this indirectly: if the rows were built with snapshot.active_id='default',
    # then fred's row would NOT be marked active. Patch persona_rows to capture the
    # active_id argument.
    import harness.tui.app as app_mod
    from harness import persona_select as ps
    captured = {}
    real_persona_rows = None
    try:
        import harness.tui.roster as roster_mod
        real_persona_rows = roster_mod.persona_rows
        def spy_rows(personas, active_id, name_of, *args, **kwargs):
            captured["active_id"] = active_id
            return real_persona_rows(personas, active_id, name_of, *args, **kwargs)
        roster_mod.persona_rows = spy_rows
        # _persona_rows is called when the rail is opened
        app._persona_rows()
    finally:
        if real_persona_rows is not None:
            roster_mod.persona_rows = real_persona_rows
    assert captured.get("active_id") == "fred", (
        f"_persona_rows must pass 'fred' as active_id (got {captured.get('active_id')!r}); "
        "it must use _current_persona() not _snapshot.active_id"
    )


# ---- FIX 4: Esc closes rail only when no turn active ----

def test_esc_cancels_turn_even_when_rail_open():
    """Esc while the rail is open AND a turn is active must let Esc fall through to
    action_cancel (it must NOT close the rail and eat the event). Before FIX 4,
    the rail-close path called event.stop() unconditionally, so action_cancel never
    fired."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import AgentRail
            # Open the rail
            app.action_toggle_rail()
            await pilot.pause()
            rail = app.query_one("#agent-rail", AgentRail)
            assert app.query_one("#agent-drawer").display is True, "rail must be open for this test"

            # Simulate a turn in flight
            app._turn_active = True

            # Track whether action_cancel was called
            cancel_called = {"v": False}
            original_cancel = app.action_cancel
            async def fake_cancel():
                cancel_called["v"] = True
            app.action_cancel = fake_cancel

            # Press Esc — should NOT close the rail; should reach action_cancel
            await pilot.press("escape")
            await pilot.pause()

            # Rail must still be open (not closed by on_key)
            assert app.query_one("#agent-drawer").display is True, (
                "Esc with turn active must NOT close the rail "
                "(it should fall through to action_cancel)"
            )
            # ...and Esc must actually reach action_cancel (the "Cancel turn" binding).
            assert cancel_called["v"] is True, (
                "Esc with turn active must fall through to action_cancel"
            )

    asyncio.run(go())


def test_esc_closes_rail_when_no_turn_active():
    """Esc while the rail is open and no turn is active must close the rail (existing
    behaviour must still work when _turn_active is False)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            from harness.tui.widgets.agent_rail import AgentRail
            from harness.tui.widgets.prompt_area import PromptArea
            # Open the rail and focus it
            app.action_toggle_rail()
            await pilot.pause()
            rail = app.query_one("#agent-rail", AgentRail)
            rail.focus()
            await pilot.pause()
            assert app.query_one("#agent-drawer").display is True
            assert app._turn_active is False

            # Esc — should close the rail
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#agent-drawer").display is False, "Esc with no turn must close the rail"

    asyncio.run(go())


# ---- FIX 5: yolo-pin chip reads from launch persona, not always default ----

def test_yolo_pinned_reads_launch_persona(tmp_path, monkeypatch):
    """HarnessTui.__init__ must call _config.yolo_pinned(persona or 'default'),
    not _config.yolo_pinned() with no arg (which always reads the default persona).
    Before FIX 5, launching as 'fred' with fred.yolo_pinned=True would produce
    _yolo_pinned=False because it read the default persona's pin."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from harness import config
    # fred has yolo_pinned=True, default does not
    config.update_agent("fred", backend="mock", model="m-fred", yolo_pinned=True)
    config.update_default(backend="mock", model="m-default", yolo_pinned=False)

    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock", persona="fred")
    assert app._yolo_pinned is True, (
        "app._yolo_pinned must be True when fred.yolo_pinned=True; "
        "HarnessTui.__init__ must call _config.yolo_pinned('fred'), not yolo_pinned()"
    )


def test_yolo_pinned_default_persona_still_works(tmp_path, monkeypatch):
    """Launching without a persona (defaults to 'default') must still read the
    default persona's yolo_pinned — the FIX 5 change must not break this."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from harness import config
    config.update_default(backend="mock", model="m-default", yolo_pinned=True)

    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")  # no persona=
    assert app._yolo_pinned is True, (
        "default persona's yolo_pinned must still be read when no persona= is passed"
    )


# ---- Task 5: on_persona_selected wires rail selection to in-process switch ----

def test_persona_selected_switches_when_idle():
    """Selecting a persona while idle calls ext_method("harness/set_persona"),
    repoints _session_id to the returned session_id, and applies PersonaResolved
    so the snapshot's active_id reflects the new persona."""
    async def go():
        from harness.tui.widgets.agent_rail import PersonaSelected as _PersonaSelected

        class _FakeConn:
            def __init__(self):
                self.ext_calls = []
                self.set_persona_response = None

            async def ext_method(self, method, params):
                self.ext_calls.append((method, params))
                if method == "harness/set_persona":
                    return self.set_persona_response
                return {}

        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            conn = _FakeConn()
            conn.set_persona_response = {
                "ok": True,
                "id": "ana",
                "session_id": "sess-ana",
                "model": "m-ana",
            }
            app._conn = conn
            app._session_id = "old-session"
            app._turn_active = False

            await app.on_persona_selected(_PersonaSelected("ana"))
            await pilot.pause()

            assert ("harness/set_persona", {"id": "ana"}) in conn.ext_calls, (
                "ext_method must be called with ('harness/set_persona', {'id': 'ana'})"
            )
            assert app._session_id == "sess-ana", (
                f"_session_id must be repointed to 'sess-ana', got {app._session_id!r}"
            )
            assert app._snapshot.active_id == "ana", (
                f"snapshot.active_id must be 'ana' after switch, got {app._snapshot.active_id!r}"
            )

    asyncio.run(go())


def test_persona_selected_inert_mid_turn():
    """Selecting a persona while a turn is active must queue the switch (not apply it):
    ext_method must NOT be called immediately, _session_id must remain unchanged,
    but _pending_persona must be set so the switch fires on turn-end."""
    async def go():
        from harness.tui.widgets.agent_rail import PersonaSelected as _PersonaSelected

        class _FakeConn:
            def __init__(self):
                self.ext_calls = []

            async def ext_method(self, method, params):
                self.ext_calls.append((method, params))
                return {}

        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            conn = _FakeConn()
            app._conn = conn
            app._session_id = "original-session"
            app._turn_active = True

            await app.on_persona_selected(_PersonaSelected("ana"))
            await pilot.pause()

            assert ("harness/set_persona", {"id": "ana"}) not in conn.ext_calls, (
                "ext_method must NOT be called with set_persona while turn is active"
            )
            assert app._session_id == "original-session", (
                "_session_id must remain unchanged while turn is active"
            )
            assert app._pending_persona == "ana", (
                "_pending_persona must be set so the switch fires on turn-end"
            )

    asyncio.run(go())


def test_new_persona_modal_enter_dismisses_with_name():
    """Typing a name and pressing Enter dismisses the modal with that name."""
    async def go():
        from harness.tui.widgets.new_persona_modal import NewPersonaModal
        from textual.app import App

        class _Host(App):
            result = "UNSET"

            def on_mount(self):
                self.push_screen(NewPersonaModal(), lambda r: setattr(self, "result", r))

        app = _Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.screen.query_one("#new-persona-name")
            inp.value = "fred"
            await pilot.press("enter")
            await pilot.pause()
        assert app.result == "fred"

    asyncio.run(go())


def test_new_persona_modal_empty_name_ignored():
    """Pressing Enter on an empty name keeps the modal open; esc dismisses with None."""
    async def go():
        from harness.tui.widgets.new_persona_modal import NewPersonaModal
        from textual.app import App

        class _Host(App):
            result = "UNSET"

            def on_mount(self):
                self.push_screen(NewPersonaModal(), lambda r: setattr(self, "result", r))

        app = _Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")     # empty -> ignored, modal stays
            await pilot.pause()
            assert app.result == "UNSET"   # not dismissed
            await pilot.press("escape")
            await pilot.pause()
        assert app.result is None

    asyncio.run(go())


# ---- Task 4: rail 'n' key + create-then-switch wiring ----

def test_rail_n_opens_new_persona_modal():
    """Pressing 'n' in the agent rail opens NewPersonaModal."""
    async def go():
        from harness.tui.widgets.new_persona_modal import NewPersonaModal

        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_toggle_rail()
            await pilot.pause()
            rail = app.query_one("#agent-rail")
            rail.focus()
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, NewPersonaModal), (
                f"Expected NewPersonaModal on screen, got {type(app.screen)}"
            )

    asyncio.run(go())


def test_do_create_persona_returns_resp():
    """_do_create_persona returns the ext_method resp dict (success case)."""
    async def go():
        class _FakeConn:
            def __init__(self):
                self.ext_calls = []

            async def ext_method(self, method, params):
                self.ext_calls.append((method, params))
                return {"ok": True, "id": "fred", "session_id": "sess-fred", "model": "m-fred"}

        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            conn = _FakeConn()
            app._conn = conn

            resp = await app._do_create_persona("fred")
            await pilot.pause()

            assert ("harness/create_persona", {"id": "fred", "display_name": "fred"}) in conn.ext_calls, (
                "ext_method must be called with ('harness/create_persona', {'id': 'fred', 'display_name': 'fred'})"
            )
            assert resp == {"ok": True, "id": "fred", "session_id": "sess-fred", "model": "m-fred"}, (
                f"_do_create_persona must return the raw resp dict, got {resp!r}"
            )

    asyncio.run(go())


def test_do_create_persona_returns_resp_error():
    """_do_create_persona returns the error resp dict without raising or changing session."""
    async def go():
        class _FakeConn:
            def __init__(self):
                self.ext_calls = []

            async def ext_method(self, method, params):
                self.ext_calls.append((method, params))
                return {"ok": False, "error": "persona 'fred' already exists"}

        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            conn = _FakeConn()
            app._conn = conn
            app._session_id = "keep"

            resp = await app._do_create_persona("fred")
            await pilot.pause()

            assert ("harness/create_persona", {"id": "fred", "display_name": "fred"}) in conn.ext_calls, (
                "ext_method must be called even when the response is an error"
            )
            assert resp.get("ok") is False, f"resp must have ok=False, got {resp!r}"
            assert app._session_id == "keep", (
                f"_session_id must remain unchanged, got {app._session_id!r}"
            )

    asyncio.run(go())


def test_do_create_persona_slugs_and_carries_display_name():
    async def go():
        class _FakeConn:
            def __init__(self): self.ext_calls = []
            async def ext_method(self, method, params):
                self.ext_calls.append((method, params))
                return {"ok": True, "id": params["id"], "session_id": "s", "model": None}
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conn = _FakeConn()
            resp = await app._do_create_persona("My Persona")
            assert resp["ok"] is True
            method, params = app._conn.ext_calls[-1]
            assert method == "harness/create_persona"
            assert params == {"id": "my-persona", "display_name": "My Persona"}
    asyncio.run(go())


def test_do_create_persona_empty_slug_rejected_no_ext_call():
    async def go():
        class _FakeConn:
            def __init__(self): self.ext_calls = []
            async def ext_method(self, method, params):
                self.ext_calls.append((method, params)); return {}
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conn = _FakeConn()
            resp = await app._do_create_persona("!!!")
            assert resp["ok"] is False
            assert app._conn.ext_calls == []          # never reached the engine
    asyncio.run(go())


def test_modal_previews_the_slug():
    async def go():
        from harness.tui.widgets.new_persona_modal import NewPersonaModal
        from textual.widgets import Input, Static
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(NewPersonaModal(slugify=lambda s: __import__(
                "harness.persona_select", fromlist=["slugify_persona_name"]
            ).slugify_persona_name(s)))
            await pilot.pause()
            modal = app.screen
            inp = modal.query_one("#new-persona-name", Input)
            inp.value = "My Persona"
            # fire the Input.Changed handler
            await pilot.pause()
            from textual.widgets import Input as _I
            modal._on_changed(_I.Changed(inp, "My Persona"))
            await pilot.pause()
            status = str(modal.query_one("#new-persona-status", Static).content)
            assert "my-persona" in status
    asyncio.run(go())


def test_create_inert_mid_turn():
    """on_new_persona_requested is a no-op (does not push modal) when _turn_active is True."""
    async def go():
        from types import SimpleNamespace
        from harness.tui.widgets.new_persona_modal import NewPersonaModal

        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._turn_active = True
            app._session_id = "keep"

            event = SimpleNamespace(stop=lambda: None)
            app.on_new_persona_requested(event)
            await pilot.pause()

            assert not isinstance(app.screen, NewPersonaModal), (
                "NewPersonaModal must NOT be pushed while _turn_active is True"
            )
            assert app._session_id == "keep", (
                f"_session_id must remain 'keep' mid-turn, got {app._session_id!r}"
            )

    asyncio.run(go())


def test_modal_drives_create_lifecycle():
    """Integration (M5): modal→create→dismiss→apply handoff end to end.

    NewPersonaModal with a fake on_create returning ok=True dismisses with
    the resp dict and _done applies the switch (session repointed, snapshot updated).
    This is the path that was untested and let I1 slip: the modal must own the
    create while staying open (spinner) and only dismiss on success.
    """
    async def go():
        from harness.tui.widgets.new_persona_modal import NewPersonaModal

        ok_resp = {
            "ok": True,
            "id": "fred",
            "session_id": "sess-fred",
            "model": "m-fred",
        }
        create_calls = []

        async def fake_create(name: str) -> dict:
            create_calls.append(name)
            return ok_resp

        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._session_id = "old-session"

            result_holder = []

            def _done(resp):
                if resp:
                    result_holder.append(resp)
                    app._apply_persona_switch(resp)

            app.push_screen(NewPersonaModal(on_create=fake_create), _done)
            await pilot.pause()

            # Modal is now on screen; fill in the name and submit
            modal = app.screen
            assert isinstance(modal, NewPersonaModal), (
                f"Expected NewPersonaModal on screen, got {type(modal)}"
            )
            modal.query_one("#new-persona-name", __import__("textual.widgets", fromlist=["Input"]).Input).value = "fred"
            await pilot.press("enter")
            # Give the worker time to complete
            for _ in range(5):
                await pilot.pause()

            assert create_calls == ["fred"], (
                f"fake_create must be called with 'fred', calls={create_calls!r}"
            )
            assert result_holder == [ok_resp], (
                f"_done must receive the resp dict, got {result_holder!r}"
            )
            assert app._session_id == "sess-fred", (
                f"_session_id must be repointed to 'sess-fred', got {app._session_id!r}"
            )
            assert app._snapshot.active_id == "fred", (
                f"snapshot.active_id must be 'fred', got {app._snapshot.active_id!r}"
            )

    asyncio.run(go())


def test_modal_error_keeps_modal_open():
    """NewPersonaModal with a fake on_create returning ok=False stays open + shows error."""
    async def go():
        from harness.tui.widgets.new_persona_modal import NewPersonaModal
        from textual.widgets import Input, Static

        async def fail_create(name: str) -> dict:
            return {"ok": False, "error": "already exists"}

        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(NewPersonaModal(on_create=fail_create))
            await pilot.pause()

            modal = app.screen
            assert isinstance(modal, NewPersonaModal), (
                f"Expected NewPersonaModal on screen, got {type(modal)}"
            )
            modal.query_one("#new-persona-name", Input).value = "fred"
            await pilot.press("enter")
            # Give the worker time to complete
            for _ in range(5):
                await pilot.pause()

            # Modal must still be on screen (set_error keeps it open)
            assert isinstance(app.screen, NewPersonaModal), (
                f"Modal must stay open on error, but screen is now {type(app.screen)}"
            )
            status_widget = modal.query_one("#new-persona-status", Static)
            status_text = str(status_widget.content)
            assert "already exists" in status_text, (
                f"Error message must appear in status, got {status_text!r}"
            )

    asyncio.run(go())


def test_apply_persona_switch_writes_visible_confirmation():
    """After a switch/create in the conversation view, a room-header line appears
    in the transcript. The status-bar chip alone is too easy to miss."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # Transition to conversation view so _started=True and #transcript exists
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(60):
                await pilot.pause()
                if app._started:
                    break
            app._active_input = lambda: type("X", (), {"focus": lambda self: None})()

            # note= path (create): should write the note text into the transcript
            app._apply_persona_switch(
                {"ok": True, "id": "fred", "session_id": "s", "model": "m"},
                note="created persona: fred — now talking to it")
            text = _transcript_text(app)
            assert "created persona: fred" in text, (
                f"create must write a visible confirmation, got {text!r}"
            )

            # plain switch path: should write "now in {name}'s conversation"
            app._apply_persona_switch(
                {"ok": True, "id": "ana", "session_id": "s2", "model": "m"})
            text = _transcript_text(app)
            assert "now in" in text and "conversation" in text, (
                f"plain switch must write room header, got {text!r}"
            )
            assert "now talking to persona:" not in text, (
                "old terse line must be gone"
            )

    asyncio.run(go())


def test_new_persona_modal_is_a_centered_box_not_fullscreen():
    """NewPersonaModal must render as a sized centered box (like SelectModal), not
    fill the whole screen — the app.tcss NewPersonaModal #new-persona-box rule
    gives it a fixed width. Regression for the "modal takes the entire screen" bug."""
    async def go():
        from harness.tui.widgets.new_persona_modal import NewPersonaModal
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(NewPersonaModal())
            await pilot.pause()
            box = app.screen.query_one("#new-persona-box")
            # the CSS pins the box to width: 72 (same as SelectModal's #select-box),
            # so it renders as a centered box, NOT full-screen. outer_size is the full
            # 72 incl. border+padding (content is 66). Without the rule it would fill.
            assert box.outer_size.width == 72, (
                f"#new-persona-box must be the fixed 72-wide centered box (like the "
                f"model picker), got outer width {box.outer_size.width}"
            )
            assert box.outer_size.width < app.size.width, "box must not fill the screen"
            assert box.styles.border.top[0] == "round", "box must have the round $accent border"

    asyncio.run(go())


# ---- structured clarification (#66) ----

def test_submit_text_starts_a_turn():
    """_submit_text routes through the same path as a typed prompt: a chosen
    option's text lands in the transcript as a user message."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            await app._submit_text("chosen option title")
            await pilot.pause()
            assert "chosen option title" in _transcript_text(app)

    asyncio.run(go())


def _decision_meta(question, options):
    return {"harness": {"decision": {
        "question": question,
        "options": [{"title": t, "rationale": r} for t, r in options]}}}


def _started_app(pilot, app):
    """Wait until the conversation transcript exists."""
    async def _wait():
        for _ in range(50):
            await pilot.pause()
            if app._started and app.query("#transcript"):
                return
    return _wait()


def test_decision_meta_pushes_modal():
    from harness.tui.messages import SessionUpdate
    from harness.tui.widgets.decision_modal import DecisionModal
    from acp import update_agent_message_text

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            await _started_app(pilot, app)
            upd = update_agent_message_text("Which did you mean?")
            upd.field_meta = _decision_meta("Which did you mean?", [("Explain", "r1"), ("Fix", "r2")])
            app.on_session_update(SessionUpdate(upd))
            await pilot.pause()
            assert any(isinstance(s, DecisionModal) for s in app.screen_stack), \
                "DecisionModal must be pushed on a decision meta"

    asyncio.run(go())


def test_decision_meta_suppresses_question_prose():
    """The question prose rides the same chunk as the decision meta; with the
    modal owning the question it must NOT also render inline in the transcript."""
    from harness.tui.messages import SessionUpdate
    from acp import update_agent_message_text

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            await _started_app(pilot, app)
            q = "Which did you mean exactly?"
            upd = update_agent_message_text(q)
            upd.field_meta = _decision_meta(q, [("Explain", "r1"), ("Fix", "r2")])
            app.on_session_update(SessionUpdate(upd))
            await pilot.pause()
            assert q not in _transcript_text(app), \
                "question prose must not render inline when a modal owns it"

    asyncio.run(go())


def _active_modal(app, cls):
    return next((s for s in app.screen_stack if isinstance(s, cls)), None)


def test_selecting_option_submits_its_title():
    from harness.tui.messages import SessionUpdate
    from harness.tui.widgets.decision_modal import DecisionModal
    from acp import update_agent_message_text

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        submitted = []
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            await _started_app(pilot, app)

            async def fake_submit(t):
                submitted.append(t)
            app._submit_text = fake_submit

            upd = update_agent_message_text("Which?")
            upd.field_meta = _decision_meta("Which?", [("Explain it", "r1"), ("Fix it", "r2")])
            app.on_session_update(SessionUpdate(upd))
            await pilot.pause()
            modal = _active_modal(app, DecisionModal)
            modal._cursor = 1          # "Fix it"
            modal.select()             # real dismiss → pops screen, fires _on_decision
            await pilot.pause()
            assert submitted == ["Fix it"]
            assert not any(isinstance(s, DecisionModal) for s in app.screen_stack), \
                "modal must be dismissed after selection"

    asyncio.run(go())


def test_esc_dismisses_modal_without_cancelling_turn():
    """esc on the modal must hit the SCREEN's binding (dismiss), not the app's
    'Cancel turn' binding — and must reset the double-push guard so future
    decisions still open."""
    from harness.tui.messages import SessionUpdate
    from harness.tui.widgets.decision_modal import DecisionModal
    from acp import update_agent_message_text

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        cancelled = []
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            await _started_app(pilot, app)

            async def fake_turn_cancel():
                cancelled.append(True)
            app.action_cancel = fake_turn_cancel   # observe the app-level binding

            upd = update_agent_message_text("Which?")
            upd.field_meta = _decision_meta("Which?", [("A", "r1"), ("B", "r2")])
            app.on_session_update(SessionUpdate(upd))
            await pilot.pause()
            assert _active_modal(app, DecisionModal) is not None
            await pilot.press("escape")
            await pilot.pause()
            assert not any(isinstance(s, DecisionModal) for s in app.screen_stack), \
                "esc must dismiss the modal"
            assert cancelled == [], "esc must NOT cancel the turn while the modal owns it"
            assert app._decision_open is False, "guard must reset so future decisions open"

    asyncio.run(go())


def test_type_something_focuses_without_submit():
    from harness.tui.messages import SessionUpdate
    from harness.tui.widgets.decision_modal import DecisionModal
    from acp import update_agent_message_text

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        submitted = []
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            await _started_app(pilot, app)

            async def fake_submit(t):
                submitted.append(t)
            app._submit_text = fake_submit

            upd = update_agent_message_text("Which?")
            upd.field_meta = _decision_meta("Which?", [("A", "r1")])
            app.on_session_update(SessionUpdate(upd))
            await pilot.pause()
            modal = _active_modal(app, DecisionModal)
            modal._cursor = modal._n   # "Type something" fallback
            modal.select()
            await pilot.pause()
            assert submitted == []                       # no turn submitted
            assert not any(isinstance(s, DecisionModal) for s in app.screen_stack)

    asyncio.run(go())


# ---- prominent agents drawer ----

def test_drawer_open_prehighlights_active_persona():
    from harness.tui.widgets.agent_rail import AgentRail

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            for _ in range(50):
                await pilot.pause()
                if app._started:
                    break
            app.action_toggle_rail()              # open the drawer
            await pilot.pause()
            rail = app.query_one("#agent-rail", AgentRail)
            assert app.query_one("#agent-drawer").display
            # the active persona's row is pre-highlighted (not forced to index 0)
            assert rail._rows[rail.index].id == app._current_persona()

    asyncio.run(go())


def test_enter_on_active_persona_is_noop_close():
    from harness.tui.widgets.agent_rail import AgentRail, PersonaSelected

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        calls = []
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            for _ in range(50):
                await pilot.pause()
                if app._started:
                    break

            async def fake_ext(method, params):
                calls.append((method, params))
                return {"ok": True, "id": params["id"], "session_id": "s"}
            app._conn.ext_method = fake_ext       # spy on set_persona

            rail = app.query_one("#agent-rail", AgentRail)
            app._show_drawer(True)
            await app.on_persona_selected(PersonaSelected(app._current_persona()))  # enter on active
            await pilot.pause()
            assert calls == []                    # no set_persona call
            assert not app.query_one("#agent-drawer").display   # drawer closed

    asyncio.run(go())


def test_highlighted_card_gets_accent_border():
    """Moving the highlight (↑↓) to a NON-active card must give that card an
    accent border — bold text alone is easy to miss. Drives a real keyboard
    'down' (the highlight is set by cursor nav, not bare .index)."""
    from textual.widgets import ListItem
    from harness.tui.widgets.agent_rail import AgentRail
    from harness.tui.roster import PersonaRow
    from harness.tui.state import AgentState

    ACCENT = (40, 108, 233)   # $accent #286CE9

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(90, 30)) as pilot:
            await _send_first_prompt(pilot, app, "hi")
            for _ in range(50):
                await pilot.pause()
                if app._started:
                    break
            app.action_toggle_rail()
            await pilot.pause()
            rail = app.query_one("#agent-rail", AgentRail)
            # two rows: active fred (index 0) + idle sam (index 1)
            rail.set_rows((
                PersonaRow(id="fred", name="Fred", active=True, status=AgentState.RUNNING_TOOL),
                PersonaRow(id="sam", name="Sam", active=False, status=AgentState.IDLE),
            ))
            rail.focus()
            await pilot.pause()
            await pilot.press("down")          # highlight the NON-active "sam"
            await pilot.pause()
            items = list(rail.query(ListItem))
            assert items[1].has_class("-highlight"), "sam should be highlighted after down"
            top = items[1].styles.border.top                  # (edge_type, Color)
            assert top and top[1].rgb == ACCENT, (
                f"highlighted non-active card must have an accent border, got {top!r}"
            )

    asyncio.run(go())


# ---- turn spacing & visual hierarchy (spec 2026-06-28-tui-turn-spacing) ----

def _classify_chip_update(task_type="chat_question"):
    """A task_classified session update with an empty body (chip only)."""
    from harness.acp_emit import with_meta, message_chunk
    meta = {"task_type": task_type, "skills": ["clarify-before-acting"], "confidence": 0.96}
    return with_meta(message_chunk(""), {"task_classified": meta})


def _transcript_kinds(app):
    """Ordered list of (kind, css_classes, text) for each transcript child, where
    kind is 'md' for a Markdown answer block or 'static' for a discrete line."""
    scroll = app.query_one("#transcript", VerticalScroll)
    out = []
    for w in scroll.children:
        if isinstance(w, Markdown):
            out.append(("md", set(w.classes), _md_source(w)))
        elif isinstance(w, Static):
            out.append(("static", set(w.classes), str(w.content)))
    return out


def test_turn_metadata_captions_carry_turn_meta_class_and_order():
    """The classification chip rides under the prompt (.turn-meta); the '▣ Build'
    run caption is a FOOTER (.turn-meta-run) below the response — order:
    user ▌ / chip / response / Build."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            app._add_user_message("explain the router")
            app.on_session_update(SessionUpdate(_classify_chip_update()))
            await pilot.pause()
            app.on_session_update(SessionUpdate(update_agent_message_text("here is the answer")))
            await pilot.pause()
            app._write_meta(4.3)
            await pilot.pause()

            kinds = _transcript_kinds(app)

        # locate the four turn elements in order
        user_i = next(i for i, (k, c, t) in enumerate(kinds) if "▌" in t)
        chip_i = next(i for i, (k, c, t) in enumerate(kinds) if "classified" in t)
        md_i = next(i for i, (k, c, t) in enumerate(kinds) if k == "md")
        build_i = next(i for i, (k, c, t) in enumerate(kinds) if RUN_CAPTION in t)

        assert user_i < chip_i < md_i < build_i, (
            f"turn elements out of order (chip rides prompt, Build is a footer): "
            f"user={user_i} chip={chip_i} md={md_i} build={build_i}\n{kinds}")
        assert "turn-meta" in kinds[chip_i][1], f"chip lacks .turn-meta: {kinds[chip_i]}"
        assert "turn-meta-run" in kinds[build_i][1], (
            f"Build run caption lacks .turn-meta-run: {kinds[build_i]}")

    asyncio.run(go())


def test_build_footer_shows_real_elapsed_appended_after_turn_end():
    """The Build run caption is appended as a footer at turn end with the real
    elapsed time — exactly one line, no placeholder, no duplicate."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            app._add_user_message("hi")
            app.on_session_update(SessionUpdate(update_agent_message_text("answer")))
            await pilot.pause()
            # mid-turn (before _write_meta): no Build footer yet
            builds = [t for k, c, t in _transcript_kinds(app) if RUN_CAPTION in t]
            assert builds == [], f"Build footer must not render before turn end: {builds}"

            app._write_meta(2.0)
            await pilot.pause()
            kinds = _transcript_kinds(app)
            builds = [t for k, c, t in kinds if RUN_CAPTION in t]

        assert len(builds) == 1, f"expected exactly one Build footer: {builds}"
        assert "2.0s" in builds[0], f"footer missing elapsed: {builds[0]}"
        # the footer sits AFTER the response markdown
        md_i = next(i for i, (k, c, t) in enumerate(kinds) if k == "md")
        build_i = next(i for i, (k, c, t) in enumerate(kinds) if RUN_CAPTION in t)
        assert md_i < build_i, f"Build footer must follow the response: {kinds}"

    asyncio.run(go())


def test_multistep_turn_emits_exactly_one_build_footer():
    """A turn with a tool call between two answer blocks emits ONE Build footer
    (one _write_meta call at turn end), after the last answer block."""
    from acp import start_tool_call

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            app._add_user_message("do a thing")
            app.on_session_update(SessionUpdate(update_agent_message_text("step one")))
            await pilot.pause()
            # a tool call interleaves (in-turn boundary → next prose opens a fresh block)
            app.on_session_update(SessionUpdate(
                start_tool_call(tool_call_id="t1", title="bash", kind="execute")))
            await pilot.pause()
            app.on_session_update(SessionUpdate(update_agent_message_text("step two")))
            await pilot.pause()
            app._write_meta(1.5)
            await pilot.pause()

            builds = [t for k, c, t in _transcript_kinds(app) if RUN_CAPTION in t]
            md_count = sum(1 for k, c, t in _transcript_kinds(app) if k == "md")

        assert len(builds) == 1, f"multi-step turn must emit exactly one Build footer: {builds}"
        assert md_count == 2, f"expected two answer blocks across the tool boundary: {md_count}"

    asyncio.run(go())


def test_tool_only_turn_still_renders_build_footer():
    """A turn that produces a tool item but NO message still renders the Build
    footer at turn end — metadata is never lost. (With the footer model this is
    just the normal append path; no special placeholder handling.)"""
    from acp import start_tool_call

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            app._add_user_message("just run it")
            app.on_session_update(SessionUpdate(
                start_tool_call(tool_call_id="t1", title="bash", kind="execute")))
            await pilot.pause()
            app._write_meta(0.7)
            await pilot.pause()

            builds = [t for k, c, t in _transcript_kinds(app) if RUN_CAPTION in t]

        assert len(builds) == 1, f"tool-only turn must still render a Build footer: {builds}"
        assert "0.7s" in builds[0], f"footer missing elapsed: {builds[0]}"

    asyncio.run(go())


def test_run_footer_hugs_response_with_turn_break_below():
    """The turn break sits ABOVE the response (margin-top 1 on the markdown) and
    BELOW the footer (margin-bottom 1 on .turn-meta-run), so prompt+chip / response
    / footer read as one group and the next prompt is separated. Both the response
    and the footer are left-indented (2)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"
            app._add_user_message("hi")
            app.on_session_update(SessionUpdate(update_agent_message_text("answer")))
            await pilot.pause()
            app._write_meta(1.0)
            await pilot.pause()
            scroll = app.query_one("#transcript", VerticalScroll)
            md = scroll.query_one(Markdown)
            run = next(w for w in scroll.children
                       if isinstance(w, Static) and "turn-meta-run" in w.classes)
            md_margin, run_margin = md.styles.margin, run.styles.margin
        # response: top break, no bottom (footer hugs it), indent 2
        assert md_margin.top == 1, f"response needs the turn-break top margin: {md_margin}"
        assert md_margin.bottom == 0, f"footer should hug the response (no md bottom): {md_margin}"
        assert md_margin.left == 2, f"response should be indented: {md_margin}"
        # footer: hugs response (no top), bottom break before next prompt, indent 2
        assert run_margin.top == 0, f"footer should hug the response above it: {run_margin}"
        assert run_margin.bottom == 1, f"footer needs the turn-break bottom margin: {run_margin}"
        assert run_margin.left == 2, f"footer should be indented: {run_margin}"

    asyncio.run(go())
