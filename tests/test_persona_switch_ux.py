import asyncio
from pathlib import Path

from harness.tui.app import HarnessTui
from harness.tui.widgets.agent_rail import PersonaSelected
from harness.tui.widgets.prompt_area import PromptArea
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static


REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [__import__("sys").executable, str(REPO / "tests/fake_agent.py")]


def _transcript_children(app):
    try:
        return list(app.query_one("#transcript", VerticalScroll).children)
    except Exception:
        return None


async def _send_first_prompt(pilot, app, text):
    app.query_one("#landing-input", PromptArea).focus()
    app.query_one("#landing-input", PromptArea).value = text
    await pilot.press("enter")


def test_persona_display_name_falls_back_to_id(tmp_path, monkeypatch):
    # default persona with no name set → returns the id "default"
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
    name = app._persona_display_name("default")
    assert isinstance(name, str) and name, "must return a non-empty string"
    # an unknown persona id with no workspace → falls back to the id verbatim
    assert app._persona_display_name("nope-nonexistent") == "nope-nonexistent"


class _FakeConn:
    def __init__(self):
        self.ext_calls = []
        self.set_persona_response = {
            "ok": True, "id": "maya", "session_id": "sess-maya", "model": "mock"}

    async def ext_method(self, method, params):
        self.ext_calls.append((method, params))
        if method == "harness/set_persona":
            return self.set_persona_response
        return {}


def _transcript_text(app):
    parts = []
    for w in (app.query_one("#transcript", VerticalScroll).children):
        if isinstance(w, Markdown):
            parts.append(getattr(w, "source", "") or "")
        elif isinstance(w, Static):
            parts.append(str(w.content))
    return "\n".join(parts)


def test_switch_clears_old_persona_messages_and_shows_room_header():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "MARKER42")
            for _ in range(60):
                await pilot.pause()
                if "MARKER42" in _transcript_text(app):
                    break
            assert "MARKER42" in _transcript_text(app), "precondition"

            app._conn = _FakeConn()
            app._turn_active = False
            await app.on_persona_selected(PersonaSelected("maya"))
            await pilot.pause()

            text = _transcript_text(app)
        assert "MARKER42" not in text, f"old persona's message bled through:\n{text}"
        assert "now in" in text and "conversation" in text, f"room header missing:\n{text}"
        assert "now talking to persona:" not in text, "old terse line should be gone"

    asyncio.run(go())


def test_mid_turn_switch_is_queued_not_immediate():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conn = _FakeConn()
            app._turn_active = True               # simulate a running turn
            before = app._current_persona()
            await app.on_persona_selected(PersonaSelected("maya"))
            await pilot.pause()
            assert app._pending_persona == "maya", "switch should be queued"
            assert app._current_persona() == before, "must NOT switch mid-turn"
            assert ("harness/set_persona", {"id": "maya"}) not in app._conn.ext_calls

    asyncio.run(go())


def test_mid_turn_switch_last_wins():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conn = _FakeConn()
            app._turn_active = True
            await app.on_persona_selected(PersonaSelected("maya"))
            await app.on_persona_selected(PersonaSelected("alex"))
            await pilot.pause()
            assert app._pending_persona == "alex", "later selection overwrites earlier"

    asyncio.run(go())


def test_pending_switch_applies_on_turn_end_before_drain():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # reach conversation state so a transcript exists
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            app._conn = _FakeConn()
            app._pending_persona = "maya"
            app._turn_active = False
            app._apply_pending_persona()          # simulate the turn-end call
            for _ in range(20):
                await pilot.pause()
            assert app._current_persona() == "maya", "pending switch did not apply"
            assert app._pending_persona is None, "pending must be cleared"

    asyncio.run(go())


def test_queued_prompt_runs_in_new_persona_room_after_switch():
    """I1 regression: a prompt queued mid-turn must be sent on the NEW session_id,
    not the old one. The switch is async (round-trip), so without the fix the drain
    races the switch and sends on the OLD session."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # reach conversation state so a transcript exists
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break

            # A slow fake conn: set_persona yields several event-loop turns to
            # simulate the subprocess round-trip. Without the fix, _drain_queue
            # fires while ext_method is still awaiting and reads the old session_id.
            prompt_session_ids: list[str | None] = []

            class _SlowFakeConn:
                ext_calls: list = []
                set_persona_response = {
                    "ok": True, "id": "maya",
                    "session_id": "sess-maya", "model": "mock",
                }

                async def ext_method(self, method, params):
                    self.ext_calls.append((method, params))
                    if method == "harness/set_persona":
                        # Multiple yields so the drain worker can race ahead
                        for _ in range(5):
                            await asyncio.sleep(0)
                        return self.set_persona_response
                    return {}

                async def prompt(self, *, prompt, session_id):
                    prompt_session_ids.append(session_id)
                    # Return a minimal response object so _send_prompt doesn't crash
                    class _Resp:
                        stop_reason = "end_turn"
                    return _Resp()

            conn = _SlowFakeConn()
            app._conn = conn
            # Stash the OLD session_id so we can assert the prompt did NOT use it
            old_session_id = app._session_id

            # Simulate end-of-turn state with a pending switch and a queued prompt
            app._turn_active = False
            app._pending_persona = "maya"
            app._queued = ["hello from new room"]

            # Trigger the combined turn-end sequence (mirrors the new finally block):
            # only drain immediately when no switch was scheduled.
            switched = app._apply_pending_persona()
            if not switched:
                app._drain_queue()

            # Allow all scheduled workers (switch + drain) to complete
            for _ in range(30):
                await pilot.pause()

            # The switch must have resolved
            assert app._session_id == "sess-maya", (
                f"session_id not updated after switch: {app._session_id!r}"
            )
            # The drained prompt must have been sent on the NEW session, not old
            assert prompt_session_ids, "conn.prompt was never called — drain did not fire"
            assert prompt_session_ids[0] == "sess-maya", (
                f"queued prompt used OLD session {prompt_session_ids[0]!r} "
                f"instead of new 'sess-maya' (old was {old_session_id!r})"
            )

    asyncio.run(go())


def test_clear_transcript_empties_children_and_resets_stream_state():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            assert _transcript_children(app), "precondition: transcript has children"
            snap_before = app._snapshot          # must be preserved
            app._clear_transcript()
            await pilot.pause()
            assert _transcript_children(app) == [], "transcript not emptied"
            assert app._streaming_md is None
            assert app._stream_buf == ""
            assert app._stream_closed is True
            assert app._boundary_after is False
            assert app._snapshot is snap_before, "_clear_transcript must NOT touch _snapshot"

    asyncio.run(go())
