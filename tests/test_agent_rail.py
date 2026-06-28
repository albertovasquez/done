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
