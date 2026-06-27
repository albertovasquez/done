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
