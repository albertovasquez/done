"""StatusChip collapse rule: glyph-only when ON (the expected default), full
labeled chip when OFF (the surprising state worth spelling out). The chip stores
its rendered markup on the widget; assert on that."""
from __future__ import annotations

from harness.tui.tokens import GLYPH
from harness.tui.widgets.status_chip import StatusChip


def _markup(chip: StatusChip) -> str:
    # StatusChip stores its label; render is f"[${token}][b]{label}[/b][/]".
    return chip._label


def test_yolo_on_is_glyph_only():
    chip = StatusChip.for_yolo(active=True, pinned=False)
    assert _markup(chip) == GLYPH["bypass"]
    assert "bypass" not in _markup(chip)  # no phrase when ON


def test_yolo_on_pinned_still_glyph_only():
    # pinned is a persistence detail; ON stays terse. Colour carries the signal.
    chip = StatusChip.for_yolo(active=True, pinned=True)
    assert _markup(chip) == GLYPH["bypass"]


def test_yolo_off_spells_it_out():
    chip = StatusChip.for_yolo(active=False, pinned=False)
    assert _markup(chip) == f"{GLYPH['bypass']} bypass OFF"


def test_compress_aware_on_is_glyph_only():
    chip = StatusChip.for_compress_aware(active=True, pinned=False)
    assert _markup(chip) == GLYPH["compress"]
    assert "compress-aware" not in _markup(chip)


def test_compress_aware_off_spells_it_out():
    chip = StatusChip.for_compress_aware(active=False, pinned=False)
    assert _markup(chip) == "compress-aware OFF"


def test_on_states_use_state_colour_tokens():
    # bypass ON is loud (error red); compress ON is calm (accent).
    assert StatusChip.for_yolo(active=True, pinned=False)._token == "error"
    assert StatusChip.for_compress_aware(active=True, pinned=False)._token == "accent"
    # OFF is muted for both.
    assert StatusChip.for_yolo(active=False, pinned=False)._token == "muted"
    assert StatusChip.for_compress_aware(active=False, pinned=False)._token == "muted"
