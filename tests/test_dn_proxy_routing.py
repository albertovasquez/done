import pytest
from harness import tui_main


def test_dn_proxy_routes_to_cli_without_launching_tui(monkeypatch):
    seen = {}
    monkeypatch.setattr("harness.proxy_service.cli.run",
                        lambda argv: (seen.__setitem__("argv", argv) or 0))
    monkeypatch.setattr(tui_main, "HarnessTui",
                        lambda *a, **k: pytest.fail("TUI must not launch for `dn proxy`"))
    rc = tui_main.main(["proxy", "status"])
    assert rc == 0
    assert seen["argv"] == ["status"]
