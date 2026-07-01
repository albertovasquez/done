# tests/test_tui_esc_precedence.py
import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from textual.widgets import Static
from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


def test_esc_calls_cancel_rpc_exactly_once():
    """One ESC press during an active turn must produce exactly one cancel() RPC.

    Without the _cancel_posted gate around the entire action_cancel body,
    on_key fires action_cancel directly AND Textual's global binding fires a
    second time (event.stop() does not suppress binding dispatch), resulting
    in two cancel() calls per ESC press.
    """
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()

            # Submit a SLOW prompt so the turn stays active long enough to ESC.
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW hello"
            await pilot.press("enter")

            # Wait until the turn is active and a connection is established.
            for _ in range(60):
                await pilot.pause()
                if app._turn_active and app._conn is not None:
                    break
            assert app._turn_active, "turn never became active — SLOW fake agent did not start"
            assert app._conn is not None, "_conn is still None after turn started"

            # Spy: wrap cancel() with a counter.
            cancel_calls = 0
            original_cancel = app._conn.cancel

            async def counting_cancel(**kwargs):
                nonlocal cancel_calls
                cancel_calls += 1
                return await original_cancel(**kwargs)

            app._conn.cancel = counting_cancel

            # Press ESC once — the double-fire must be swallowed by the gate.
            await pilot.press("escape")
            for _ in range(10):
                await pilot.pause()

            assert cancel_calls == 1, (
                f"Expected cancel() called exactly once per ESC, got {cancel_calls}. "
                "The _cancel_posted gate in action_cancel is missing or broken."
            )

            # Wait for the in-flight worker to finish so teardown doesn't crash.
            for _ in range(80):
                await pilot.pause()
                if not app._turn_active:
                    break

    asyncio.run(go())


def test_esc_during_turn_cancels_even_with_text_in_box():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        cancelled = []
        async with app.run_test() as pilot:
            await pilot.pause()
            # stub the connection's cancel to record the call
            orig = app.action_cancel
            async def spy():
                cancelled.append(True)
                await orig()
            app.action_cancel = spy
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look at file"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and app._streaming_md is None:
                    break
            # type into the box, then ESC — must cancel the turn, not just clear text
            app._active_input().value = "half typed next prompt"
            await pilot.press("escape")
            await pilot.pause()
            assert cancelled, "ESC during turn did not trigger action_cancel"
            assert app._active_input().value == "half typed next prompt", \
                "ESC during turn wrongly cleared the typed text (R6: first ESC cancels, text stays)"
            # wait for the in-flight worker to finish so teardown doesn't crash
            for _ in range(80):
                await pilot.pause()
                if not app._turn_active:
                    break
    asyncio.run(go())


def test_esc_during_turn_posts_single_canceling_line():
    """ESC during an active turn must append exactly ONE '— canceling… —' line.

    The defect: on_key calls action_cancel() directly then returns, but
    event.stop() does NOT suppress BINDINGS dispatch in Textual — the global
    BINDING ("escape", "cancel", …) fires too, so action_cancel runs twice and
    the muted feedback line appears twice.  The fix makes action_cancel
    idempotent within a single turn via _cancel_posted.
    """
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        canceling_calls = []
        async with app.run_test() as pilot:
            await pilot.pause()
            # Intercept _append_line to count "canceling" calls without
            # touching widget internals (avoids renderable attribute issues)
            orig_append = app._append_line
            def counting_append(markup, **kw):
                if "canceling" in markup.lower():
                    canceling_calls.append(markup)
                return orig_append(markup, **kw)
            app._append_line = counting_append

            # Send a SLOW prompt so there is a live turn to cancel
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look at file"
            await pilot.press("enter")
            # Wait until turn is active (same pattern as existing test)
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and app._streaming_md is None:
                    break
            # Press ESC exactly once
            await pilot.press("escape")
            # Give the event loop time to process both action_cancel invocations
            # (on_key direct call + BINDING dispatch)
            for _ in range(10):
                await pilot.pause()
            # Wait for the in-flight worker to finish so teardown doesn't crash
            for _ in range(80):
                await pilot.pause()
                if not app._turn_active:
                    break
        assert len(canceling_calls) == 1, (
            f"Expected exactly 1 '— canceling… —' append, got {len(canceling_calls)}: {canceling_calls}"
        )
    asyncio.run(go())


def test_esc_closes_agents_drawer_when_focus_on_prompt():
    """ESC must close the agents drawer even when focus is on the prompt (not the rail).

    Regression: the old guard required `isinstance(self.focused, AgentRail)`,
    so ESC was silently swallowed when the drawer was open but focus stayed
    on the prompt area.
    """
    async def go():
        from harness.tui.widgets.agent_rail import AgentRail
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # Open the drawer via Tab (standard path)
            app.query_one("#landing-input", PromptArea).focus()
            await pilot.press("tab")
            await pilot.pause()
            assert app._drawer_visible(), "drawer should be visible after Tab"
            # Move focus back to the prompt (simulates user clicking the input)
            app.query_one("#landing-input", PromptArea).focus()
            await pilot.pause()
            assert not isinstance(app.focused, AgentRail), "focus should be on prompt, not rail"
            # ESC must close the drawer
            await pilot.press("escape")
            await pilot.pause()
            assert not app._drawer_visible(), "ESC must close drawer even when focus is on prompt"

    asyncio.run(go())


def test_esc_hides_working_spinner_immediately():
    """The user's report: after cancel, the bottom spinner keeps spinning. ESC
    must stop the #working LoadingIndicator AT ONCE (in action_cancel), not only
    when the turn winds down and prompt() returns. Uses the SLOW fake-agent path
    (0.6s pre-token gap) so the spinner is up when ESC lands."""
    from textual.widgets import LoadingIndicator

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW hello"
            await pilot.press("enter")

            # Wait until the turn is active and the spinner is showing.
            spinner_seen = False
            for _ in range(60):
                await pilot.pause()
                if app._turn_active and app.query("#working"):
                    spinner_seen = True
                    break
            assert spinner_seen, "spinner (#working) never appeared during the SLOW turn"

            # ESC — the spinner must be gone right away, well before the 0.6s
            # SLOW sleep elapses and prompt() returns.
            await pilot.press("escape")
            await pilot.pause()
            assert not app.query("#working"), \
                "ESC must hide the working spinner immediately, not wait for turn-end"

            # Let the in-flight worker finish so teardown is clean, then confirm it
            # stays cleared after the turn actually ends.
            for _ in range(80):
                await pilot.pause()
                if not app._turn_active:
                    break
            assert not app.query("#working"), "spinner must stay cleared after turn-end"

    asyncio.run(go())
