"""Smoke tests for the brand-book generator: it builds without error and the
output reflects the LIVE tokens (so a token change is visible on the page, and a
broken token reference fails here instead of silently shipping a wrong page)."""

from __future__ import annotations

from harness.tui.styles import brandbook
from harness.tui.theme import HARNESS_THEME
from harness.tui.tokens import GLYPH
from harness.tui.state import AgentState, ToolStatus


def test_build_html_succeeds_and_is_self_contained():
    out = brandbook.build_html("test")
    assert out.startswith("<!doctype html>")
    assert "<style>" in out and "</style>" in out      # inline CSS, no external deps
    assert out.strip().endswith("</html>")


def test_page_uses_live_palette_hexes():
    out = brandbook.build_html("test")
    # Brand-core colors must appear (pulled from the live Theme, not hardcoded).
    for hexv in (HARNESS_THEME.accent, HARNESS_THEME.background,
                 HARNESS_THEME.success, HARNESS_THEME.error):
        assert hexv in out, f"{hexv} missing from page"


def test_no_unresolved_markup_leaks():
    # Every `[$token]` widget-markup string must be translated to HTML, never
    # rendered literally on the page.
    out = brandbook.build_html("test")
    assert "[$" not in out


def test_every_status_and_glyph_is_represented():
    out = brandbook.build_html("test")
    for st in AgentState:                     # agent-state vocabulary
        assert st.value in out
    for ts in ToolStatus:                     # tool-status vocabulary
        assert ts.value in out
    for glyph in GLYPH.values():              # the iconography
        assert glyph in out


def test_markup_translator_resolves_token_to_real_hex():
    html = brandbook.markup_to_html("[$accent][b]RUNNING[/b][/]")
    assert HARNESS_THEME.accent in html
    assert "RUNNING" in html
    assert "[$" not in html                   # fully translated


def test_usage_notes_have_single_source_in_components_md():
    # The "When to use" guidance must come ONLY from components.md (no copy in
    # the generator). Parser finds several; each shipped component we mock has one.
    notes = brandbook._parse_usage_notes()
    for name in ("StatusChip", "ActivityStatus", "ToolCallRow", "SelectModal",
                 "DecisionModal"):
        assert name in notes and notes[name], f"no usage note parsed for {name}"


def test_usage_notes_render_into_html_from_markdown():
    # A note's text in components.md must appear on the page — proving the HTML
    # pulls from the markdown rather than holding its own copy.
    notes = brandbook._parse_usage_notes()
    out = brandbook.build_html("test")
    sample = notes["StatusChip"]
    # first few words of the real note appear verbatim in the rendered page
    head = " ".join(sample.split()[:6])
    assert head in out


def test_shared_header_maps_both_component_names():
    # "### `StateDot` / `ActivityGlyph`" must give BOTH names the same note.
    notes = brandbook._parse_usage_notes()
    assert notes.get("StateDot") and notes.get("ActivityGlyph")
    assert notes["StateDot"] == notes["ActivityGlyph"]
