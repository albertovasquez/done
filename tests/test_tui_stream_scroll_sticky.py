"""Sticky-bottom auto-scroll via Textual's native anchor.

The transcript is anchored (`VerticalScroll.anchor()`): Textual keeps the view
pinned to the bottom as content streams in, but releases the anchor the moment
the user scrolls up to read earlier content — so a streaming response no longer
yanks the view down. Submitting a message is a deliberate action and force-snaps
to the bottom.

The pixel-level anchor behavior needs a real terminal layout (headless run_test
gives the nested VerticalScroll no size), so these tests assert the wiring that
delegates scrolling to the anchor rather than the settled scroll offset:
  - the transcript is anchored on entry;
  - streaming deltas do NOT call scroll_end directly (anchor owns that);
  - submitting a user message DOES force a scroll_end.
"""
import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from harness.tui.app import HarnessTui

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


def _run(body):
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            await pilot.pause()
            await body(app, pilot)
    asyncio.run(go())


def _spy_scroll_end(app):
    """Count scroll_end calls on the live transcript widget."""
    calls = {"n": 0}
    tr = app._transcript
    orig = tr.scroll_end
    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)
    tr.scroll_end = counting  # type: ignore[method-assign]
    return calls


def test_transcript_is_anchored():
    """Sticky-bottom is delegated to Textual's anchor, engaged on entry."""
    async def body(app, pilot):
        assert app._transcript.is_anchored, \
            "transcript not anchored — streaming would not follow the tail"
    _run(body)


def test_streaming_delegates_scroll_to_anchor():
    """A streaming delta must NOT call scroll_end directly; the anchor owns
    bottom-following, so a scrolled-up user is never yanked down."""
    async def body(app, pilot):
        calls = _spy_scroll_end(app)
        app._stream_message("hello ")
        await pilot.pause()
        app._stream_message("world ")
        await pilot.pause()
        assert calls["n"] == 0, \
            f"streaming called scroll_end directly ({calls['n']}x) — bypasses the anchor"
    _run(body)


def test_submit_forces_scroll_to_bottom():
    """Submitting a message is deliberate: it force-snaps to the bottom (and
    re-engages the anchor) even if the user had scrolled up."""
    async def body(app, pilot):
        calls = _spy_scroll_end(app)
        app._add_user_message("what is 2+2?")
        await pilot.pause()
        assert calls["n"] >= 1, "submitting a message did not snap to the bottom"
    _run(body)
