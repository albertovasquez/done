"""Stress repro for the "Responding…" stuck-spinner bug.

The real bug (trace 20260628-131123): a chat_question whose tiny answer +
chat.done relay + prompt RESPONSE frame all land in one ~8ms burst, after which
the TUI's ActivityRegion stays on "Responding (elapsed)" forever — i.e. the turn
ended on the wire but TurnEnded never fired in the UI, so the status widget never
cleared and the composer stayed locked.

Headless transport repro (repro_stuck.py) PROVED the agent + acp transport return
correctly for this exact shape — the answer (~1.3KB) can't fill the 64KB pipe, so
it is NOT backpressure. The remaining suspect is a Textual-event-loop timing race
between the burst delivery and prompt() resolving.

This drives the REAL HarnessTui via Pilot against the REAL fake-agent subprocess
(real stdio), replaying the BURST shape many times. After each turn it asserts the
turn actually ended in the UI: state idle, no "Responding", composer usable. A tick
where TurnEnded never fires reproduces the bug (assertion fails with the stuck
state captured).
"""
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from pathlib import Path

import pytest

from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea
from harness.tui.widgets.activity_region import ActivityRegion
from harness.tui.state import AgentState

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

# how many burst turns to run; the race is timing-dependent so we need volume
ROUNDS = int(__import__("os").environ.get("STRESS_ROUNDS", "60"))


def _stuck_reason(app) -> str | None:
    """Return a description if the turn is NOT cleanly ended, else None."""
    snap = app._snapshot.active
    reasons = []
    if app._turn_active:
        reasons.append("_turn_active still True")
    if snap is not None and snap.state in (AgentState.THINKING, AgentState.RESPONDING,
                                           AgentState.RUNNING_TOOL):
        reasons.append(f"state={snap.state} label={snap.activity_label!r}")
    ar = app.query_one("#activity-region", ActivityRegion)
    if not ar.is_idle(snap):
        reasons.append("ActivityRegion not idle")
    if app._active_input().disabled:
        reasons.append("composer disabled")
    return "; ".join(reasons) if reasons else None


def test_burst_turn_always_ends():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # first send transitions landing -> conversation
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "BURST 0"
            await pilot.press("enter")

            for i in range(ROUNDS):
                # wait (bounded) for the turn to settle
                settled = False
                for _ in range(200):                 # up to ~200 ticks
                    await pilot.pause()
                    if not app._turn_active:
                        settled = True
                        break
                assert settled, (
                    f"round {i}: turn never settled — prompt() did not resolve "
                    f"({_stuck_reason(app)})")
                # turn says settled: the status widget MUST be cleared too
                reason = _stuck_reason(app)
                assert reason is None, f"round {i}: turn ended but UI stuck — {reason}"

                # fire the next burst
                inp = app.query_one(PromptArea) if False else app._active_input()
                inp.value = f"BURST {i+1}"
                await pilot.press("enter")
            await pilot.pause()

    asyncio.run(go())
