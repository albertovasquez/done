from harness.tui.tokens import GLYPH, STATUS_LABEL


def test_glyph_has_all_state_and_subtype_keys():
    for key in ("idle", "active", "responding", "tool", "done", "failed",
                "scheduled", "awaiting", "edit", "test", "read", "shell", "search"):
        assert key in GLYPH, f"missing glyph: {key}"
        assert GLYPH[key], f"empty glyph: {key}"


def test_bypass_glyph_present():
    assert GLYPH["bypass"] == "▶▶"


def test_status_label_is_uppercase():
    assert STATUS_LABEL["running"] == "RUNNING"
    assert STATUS_LABEL["completed"] == "COMPLETED"
    assert STATUS_LABEL["scheduled"] == "SCHEDULED"
    assert STATUS_LABEL["failed"] == "FAILED"
    assert STATUS_LABEL["queued"] == "QUEUED"
    # Verify all status labels in the dictionary are uppercase
    for key, label in STATUS_LABEL.items():
        assert label == label.upper(), f"{key} label not uppercase: {label}"


def test_theme_has_product_status_tokens():
    from harness.tui.theme import COLORS, STATUS_COLOR, HARNESS_THEME
    # green/amber are sanctioned product-status tokens (spec §4.1)
    assert COLORS["success"] == "#7ee787"
    assert COLORS["scheduled"] == "#e3b341"
    assert STATUS_COLOR["scheduled"] == "#e3b341"
    assert HARNESS_THEME.variables["scheduled"] == "#e3b341"
