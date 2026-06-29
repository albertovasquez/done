import pytest
from harness import tui_main


def test_dn_cron_routes_to_cli_without_launching_tui(monkeypatch):
    seen = {}
    def _fake_cli_run(argv):
        seen["argv"] = argv
        return 0
    monkeypatch.setattr("harness.jobs.cli.run", _fake_cli_run)
    # If routing works, the TUI app is never constructed:
    monkeypatch.setattr(tui_main, "HarnessTui",
                        lambda *a, **k: pytest.fail("TUI must not launch for `dn cron`"))
    rc = tui_main.main(["cron", "install"])
    assert rc == 0
    assert seen["argv"] == ["install"]


def test_bare_dn_still_reaches_tui_arg_parsing(monkeypatch):
    # A non-cron invocation must NOT be intercepted; it proceeds to arg parsing.
    # Stub the app so we don't spawn a real agent; assert we got past routing.
    launched = {}

    class _FakeApp:
        def run(self): pass

    def _fake_tui(*a, **k):
        launched["yes"] = True
        return _FakeApp()

    monkeypatch.setattr(tui_main, "HarnessTui", _fake_tui)
    monkeypatch.setattr("harness.paths.load_env", lambda *a, **k: None)
    tui_main.main(["--model", "mock", "--cwd", "."])
    assert launched.get("yes") is True
