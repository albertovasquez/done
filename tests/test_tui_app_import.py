def test_app_constructs_without_running():
    from harness.tui.app import HarnessTui, PermissionModal
    app = HarnessTui(agent_cmd=["x"], cwd=".", model="mock")
    assert app.agent_cmd == ["x"]
    assert app.cwd == "."
    assert app.model == "mock"
    assert PermissionModal  # importable


def test_header_uses_brand_e_mark():
    # The landing header was redesigned from the "DoneDone" wordmark to the
    # standalone ≡ brand mark + tagline (commits d0a61a4, dda2d8e). Assert the
    # current design: the ≡ mark and the tagline are present, and the old
    # wordmark is gone.
    from harness.tui.app import HarnessTui
    app = HarnessTui(agent_cmd=["x"], cwd=".", model="mock", version="0.5.0")
    markup = app._header_markup()
    assert "≡" in markup
    assert "Get Shit Done" in markup           # tagline
    assert "DoneDone" not in markup            # old wordmark removed
    assert "[b]DONE[/b]" not in markup
