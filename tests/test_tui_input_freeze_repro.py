"""Repro for the reported "can't type / clicking input does nothing" freeze
during the agent-path pre-stream window.

User report (trace 20260628-151948 turn 2, code_explain over a 12.9KB file):
the composer was unresponsive while the spinner was up and NOTHING had printed
yet — keystrokes did not appear and clicking the input did not focus it. That
"spinner, nothing printing" phase is the ~4.5s uncached router classify before
the first frame reaches the TUI.

This drives the REAL HarnessTui via Pilot against the REAL fake-agent subprocess.
The fake "SLOW" prompt emits the task chip, then stalls 0.6s emitting nothing,
then returns — reproducing the pre-stream gap. DURING that gap we assert the
composer is usable: not disabled, focusable, and a typed key lands in its value.

If input is alive here, the test PASSES — which REFUTES "the event loop is frozen"
and points the investigation at perception/render. If it FAILS, the freeze is
reproduced as a real input-handling bug.
"""
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from pathlib import Path

from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


def test_composer_usable_during_pre_stream_gap():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # send a SLOW turn: chip emits immediately, then a 0.6s silent gap
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW please look at the file"
            await pilot.press("enter")

            # wait until the turn is in flight but NO prose chunk has arrived yet
            # (the "spinner, nothing printing" window). Bound the wait.
            in_window = False
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and app._streaming_md is None:
                    in_window = True
                    break
            assert in_window, "never observed the pre-stream window (turn active, no chunk)"

            # --- PROBE: is the composer usable right now? ---
            inp = app._active_input()
            assert not inp.disabled, "composer is DISABLED during the pre-stream gap"

            inp.focus()
            await pilot.pause()
            assert app.focused is inp, (
                f"composer did not take focus during the gap (focused={app.focused!r})")

            before = inp.value
            await pilot.press("x")
            await pilot.pause()
            assert inp.value == before + "x", (
                f"keystroke did not land during the gap "
                f"(value {before!r} -> {inp.value!r})")

            # let the turn finish so the app tears down cleanly
            for _ in range(50):
                await pilot.pause()
                if not app._turn_active:
                    break

    asyncio.run(go())


def test_mouse_click_focuses_composer_during_pre_stream_gap():
    """The most faithful repro: during the pre-stream gap, drive a real MOUSE
    CLICK on the composer (not a programmatic .focus()) and type without
    pre-focusing — exactly the user's reported actions ("clicked input, it
    didn't become active; keystrokes didn't appear")."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look at the file"
            await pilot.press("enter")

            in_window = False
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and app._streaming_md is None:
                    in_window = True
                    break
            assert in_window, "never observed the pre-stream window"

            inp = app._active_input()
            # blur first so we know the click is what re-focuses it
            app.set_focus(None)
            await pilot.pause()

            await pilot.click("#conversation-input")
            await pilot.pause()
            assert app.focused is inp, (
                f"mouse click did NOT focus the composer during the gap "
                f"(focused={app.focused!r})")

            before = inp.value
            await pilot.press("z")
            await pilot.pause()
            assert inp.value == before + "z", (
                f"keystroke after click did not land (value {before!r} -> {inp.value!r})")

            for _ in range(50):
                await pilot.pause()
                if not app._turn_active:
                    break

    asyncio.run(go())
