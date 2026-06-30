from harness.tui.widgets.proxy_login_modal import provider_rows


def test_rows_render_status_and_mechanism():
    rows = provider_rows(status={"anthropic": True, "xai": False})
    anth = next(r for r in rows if r["id"] == "anthropic")
    xai = next(r for r in rows if r["id"] == "xai")
    assert anth["mark"] == "✓" and "browser" in anth["hint"]
    assert xai["mark"] == "✗" and "CLI" in xai["hint"]
