"""The SelectModal's current/highlighted row must be VISIBLY highlighted (accent
tint) even while focus is on the search box. Regression: the CSS highlight rule
used Textual's wrong class name `.--highlight` (double-dash) instead of the real
`.-highlight` (single-dash), so the tint never applied — the current model showed
the ● dot but rendered as plain surface, looking un-selected (user had to
navigate to it). Guards both the index landing on the current selectable row and
the highlight actually being styled."""
import asyncio

from textual.widgets import ListView

from harness.tui.app import HarnessTui
from harness.tui.widgets.select_modal import SelectModal, SelectOption

_FAKE_CMD = ["python", "-c", "import sys,time;sys.stdout.write('{}');time.sleep(30)"]

_ROWS = [
    SelectOption(id="", label="— anthropic —", group="anthropic", disabled=True),
    SelectOption(id="claude-opus-4-8", label="Claude Opus 4.8", group="anthropic"),
    SelectOption(id="claude-sonnet-5", label="Claude Sonnet 5", group="anthropic"),
]


def _drive(current):
    """Push a SelectModal into the real app; return (index, data, has_class, bg)."""
    result = {}

    async def go():
        app = HarnessTui(agent_cmd=_FAKE_CMD, cwd=".", model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = SelectModal(title="Select model", options=_ROWS, current=current)
            await app.push_screen(modal)
            await pilot.pause()
            lv = modal.query_one("#select-list", ListView)
            hl = lv.highlighted_child
            result["index"] = lv.index
            result["data"] = getattr(hl, "data", None) if hl else None
            result["has_highlight"] = hl.has_class("-highlight") if hl else False
            result["bg"] = hl.styles.background if hl else None
            result["focused"] = app.focused.id if app.focused else None

    asyncio.run(go())
    return result


def test_current_row_is_highlighted_and_visibly_tinted():
    r = _drive(current="claude-opus-4-8")
    # active row is the current model (index 1), NOT the disabled header (index 0)
    assert r["index"] == 1
    assert r["data"] == "claude-opus-4-8"
    assert r["has_highlight"] is True
    # focus is on the search box — the highlight must STILL render a tint
    assert r["focused"] == "select-search"
    # Discriminating signal (measured): our rule `$accent 30%` resolves the
    # highlighted bg to the accent color at alpha 0.3. With the old `.--highlight`
    # typo the rule never matches and the row falls back to an OPAQUE surface color
    # (alpha 1.0, no accent) — which reads as un-highlighted. Assert the accent
    # tint specifically, not merely "some background".
    bg = r["bg"]
    assert bg is not None, "highlighted row has no background"
    assert round(getattr(bg, "a", 1.0), 2) == 0.30, (
        f"highlight not the accent tint (a=0.3); got {bg!r} — the `.-highlight` "
        f"CSS rule is not applying (regressed to `.--highlight`?)")


def test_falls_back_to_first_selectable_when_no_current():
    r = _drive(current=None)
    assert r["index"] == 1                       # skips the disabled header at 0
    assert r["data"] == "claude-opus-4-8"
    assert r["has_highlight"] is True
