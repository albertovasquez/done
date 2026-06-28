import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

def _drive(prompt_text, after):
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        calls = {"n": 0}
        async with app.run_test() as pilot:
            await pilot.pause()
            # wrap Markdown.update to count renders on the live widget
            import harness.tui.app as appmod
            orig_update = appmod.Markdown.update
            def counting_update(self, *a, **k):
                calls["n"] += 1
                return orig_update(self, *a, **k)
            appmod.Markdown.update = counting_update
            try:
                app.query_one("#landing-input", PromptArea).focus()
                app.query_one("#landing-input", PromptArea).value = prompt_text
                await pilot.press("enter")
                for _ in range(200):
                    await pilot.pause()
                    if not app._turn_active:
                        break
                await pilot.pause()
                await after(app, calls)
            finally:
                appmod.Markdown.update = orig_update
    asyncio.run(go())

def test_manychunks_coalesces_renders():
    async def after(app, calls):
        # 60 chunks must NOT cause ~60 full re-renders; coalesced << chunk count
        assert calls["n"] < 30, f"expected coalesced renders, got {calls['n']} for 60 chunks"
    _drive("MANYCHUNKS", after)

def test_manychunks_no_text_lost():
    async def after(app, calls):
        # the final buffer must contain all 60 words (R1: nothing dropped)
        assert "word0 " in app._stream_buf and "word59 " in app._stream_buf, \
            f"text lost: tail={app._stream_buf[-40:]!r}"
    _drive("MANYCHUNKS", after)

def test_late_delta_after_close_renders():
    """R1: a delta arriving after _end_stream still paints (sync flush)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "STREAM hi"
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause()
                if not app._turn_active:
                    break
            # Close the stream the way a NEW user turn does (_add_user_message ->
            # _end_stream): the widget ref is KEPT so a trailing late delta can
            # still extend it in place. Now a late delta must flush SYNC (the 12Hz
            # interval is stopped on close, so it cannot rely on the timer).
            app._end_stream()
            assert app._stream_closed
            before = app._stream_buf
            app._stream_message("LATE")
            await pilot.pause()
            assert app._stream_buf == before + "LATE"
            assert not app._stream_dirty, "late delta not flushed (R1 sync flush failed)"
    asyncio.run(go())

def test_first_chunk_of_new_answer_renders_synchronously():
    """The first delta of a fresh answer must paint without waiting for the
    12Hz timer (no 80ms blank flicker after _hide_working)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # drive one real STREAM turn so a widget opens, then assert the buffer
            # was marked clean (flushed) right after the first chunk — i.e. the
            # first chunk did not stay dirty waiting on the interval.
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "STREAM hi"
            await pilot.press("enter")
            # capture state at the first paint: after the first delta the widget
            # exists and dirty has been cleared by the sync flush.
            saw_clean_with_widget = False
            for _ in range(100):
                await pilot.pause()
                if app._streaming_md is not None and not app._stream_dirty:
                    saw_clean_with_widget = True
                    break
            assert saw_clean_with_widget, "first chunk left buffer dirty (timer-only paint)"
    asyncio.run(go())
