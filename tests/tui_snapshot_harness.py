"""Deterministic boot + drive helpers for TUI snapshot tests.

Single place that knows how to bring HarnessTui to a known VISUAL state for
`snap_compare`. Mirrors the proven idiom in tests/test_tui_pilot.py: construct a
live app with the fake-agent subprocess attached, then poll pilot.pause() until
the turn has settled (this codebase waits on a settled condition, not a
TurnEnded event object)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


@pytest.fixture
def isolated_default_persona(monkeypatch, tmp_path_factory):
    """XDG isolation so the footer run-caption is a deterministic '▣ Bob' on any
    box, independent of the developer's real ~/.config."""
    cfg = tmp_path_factory.mktemp("xdg_config")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    from harness import persona
    persona.seed_default_workspace()


async def drive_completed_turn(pilot, app, prompt: str) -> None:
    """Landing -> conversation, send `prompt`, wait until the answer has settled.

    Capture only AFTER this returns, so the SVG is a frozen completed turn."""
    from harness.tui.state import AgentState
    from harness.tui.widgets.prompt_area import PromptArea
    from textual.containers import VerticalScroll
    from textual.css.query import NoMatches
    from textual.widgets import Markdown

    app.query_one("#landing-input", PromptArea).focus()
    app.query_one("#landing-input", PromptArea).value = prompt
    await pilot.press("enter")

    # 1) wait for the conversation view to exist (transition happened)
    for _ in range(50):
        await pilot.pause()
        if getattr(app, "_started", False) and app.query("#transcript"):
            break
    assert getattr(app, "_started", False) and app.query("#transcript"), (
        "landing->conversation transition never completed within the settle budget"
    )

    # 2) wait for the streamed answer to be present AND stable across two ticks,
    #    AND the turn to have actually reached a terminal AgentState. Text
    #    stability alone is not enough: a slow stream can pause two ticks
    #    mid-answer and look "settled" while still RESPONDING.
    prev = None
    stable = 0
    cur = None
    for _ in range(80):
        await pilot.pause()
        try:
            scroll = app.query_one("#transcript", VerticalScroll)
        except NoMatches:
            continue
        mds = [w for w in scroll.children if isinstance(w, Markdown)]
        cur = "".join(
            (getattr(m, "source", None) or getattr(m, "_markdown", "") or "")
            for m in mds
        )
        turn_done = app._snapshot.active.state in (AgentState.DONE, AgentState.FAILED)
        if cur and cur == prev:
            stable += 1
            if stable >= 2 and turn_done:      # stable text AND terminal state => settled
                break
        else:
            stable = 0
        prev = cur
    assert cur, "streamed answer never appeared within the settle budget"
    assert app._snapshot.active.state in (AgentState.DONE, AgentState.FAILED), (
        "turn never reached a terminal AgentState within the settle budget"
    )

    await pilot.pause()          # final drain before the caller captures
