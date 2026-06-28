from harness.tui.widgets.quick_keys import QUICK_KEYS, quick_keys_markup  # noqa: E402


def test_quick_keys_lists_working_keys():
    keys = [k for k, _ in QUICK_KEYS]
    assert "tab" in keys and "enter" in keys and "/" in keys
    md = quick_keys_markup()
    assert "QUICK KEYS" in md
    for _, label in QUICK_KEYS:
        assert label in md


def test_quick_keys_does_not_list_unbound_keys():
    # legend documents real keys only — no '?' help unless it's actually bound
    assert "?" not in [k for k, _ in QUICK_KEYS]
