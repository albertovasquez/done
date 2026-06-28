import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.roster import PersonaRow  # noqa: E402
from harness.tui.state import AgentState  # noqa: E402
from harness.tui.widgets.agent_rail import card_markup  # noqa: E402


def test_active_card_name_is_accent_bold_with_real_status():
    row = PersonaRow(id="fred", name="Fred", active=True, status=AgentState.RUNNING_TOOL)
    out = card_markup(row, "2 tasks")
    assert "Fred" in out
    assert "$accent" in out and "[b]" in out       # active name styling
    assert "RUNNING" in out                          # real status label
    assert "2 tasks" in out                          # sub-line


def test_idle_card_is_foreground_with_idle_status():
    row = PersonaRow(id="sam", name="Sam", active=False, status=AgentState.IDLE)
    out = card_markup(row, "idle")
    assert "Sam" in out
    assert "$foreground" in out                       # non-active name
    assert "IDLE" in out
    assert "idle" in out


def test_card_has_no_icon_tile_glyph():
    # the brand ≡ tile was dropped; it must not appear
    out = card_markup(PersonaRow(id="x", name="X", active=True), "idle")
    assert "≡" not in out


# ---- set_rows: cards + pre-highlight the active row ----

import asyncio  # noqa: E402

from textual.app import App  # noqa: E402
from textual.widgets import ListItem  # noqa: E402

from harness.tui.widgets.agent_rail import AgentRail  # noqa: E402


class _Host(App):
    def compose(self):
        yield AgentRail(id="r")


def test_set_rows_renders_cards_and_preselects_active():
    rows = (PersonaRow(id="default", name="default", active=False),
            PersonaRow(id="fred", name="Fred", active=True, status=AgentState.RUNNING_TOOL))

    async def go():
        app = _Host()
        async with app.run_test() as pilot:
            rail = app.query_one("#r", AgentRail)
            rail.set_rows(rows)
            await pilot.pause()
            assert rail.index == 1                      # pre-highlight the active row (fred)
            items = list(rail.query(ListItem))
            assert len(items) == 2
            assert items[1].has_class("active")         # active row tagged
            assert items[0].has_class("persona-card")

    asyncio.run(go())
