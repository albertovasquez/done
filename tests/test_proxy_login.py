from harness.proxy_service import login, management


def test_browser_poll_opens_url(monkeypatch):
    opened = {}
    monkeypatch.setattr("harness.proxy_service.management.auth_url",
                        lambda p, pw, base=None: ("https://x", "st-1"))
    h = login.start("anthropic", "pw",
                    open_browser=lambda url: opened.__setitem__("url", url),
                    run_subprocess=lambda argv: 0)
    assert opened["url"] == "https://x" and h.state == "st-1"


def test_cli_flag_runs_subprocess(monkeypatch):
    ran = {}
    h = login.start("xai", "pw",
                    open_browser=lambda url: None,
                    run_subprocess=lambda argv: ran.__setitem__("argv", argv) or 0)
    assert "--xai-login" in ran["argv"]


def test_api_key_provider_returns_docs_sentinel():
    h = login.start("gemini", "pw", open_browser=lambda u: None, run_subprocess=lambda a: 0)
    assert h.mechanism == "api_key"


def test_run_cli_login_browser_success(monkeypatch):
    monkeypatch.setattr(management, "auth_url", lambda p, pw, base=None: ("https://x", "st"))
    polls = iter(["pending", "pending", "ok"])
    out = []
    ok = login.run_cli_login("codex", "pw",
        open_browser=lambda u: True, poll=lambda s, pw, base=None: next(polls),
        sleep=lambda s: None, out=out.append, attempts=5)
    assert ok is True
    assert any("waiting" in m.lower() or "browser" in m.lower() for m in out)


def test_run_cli_login_headless_prints_url(monkeypatch):
    monkeypatch.setattr(management, "auth_url", lambda p, pw, base=None: ("https://X", "st"))
    out = []
    ok = login.run_cli_login("codex", "pw",
        open_browser=lambda u: False,                 # no browser
        poll=lambda s, pw, base=None: "ok", sleep=lambda s: None, out=out.append, attempts=2)
    assert ok is True
    assert any("https://X" in m for m in out)         # URL printed for manual open


def test_run_cli_login_timeout_returns_false(monkeypatch):
    monkeypatch.setattr(management, "auth_url", lambda p, pw, base=None: ("https://x", "st"))
    ok = login.run_cli_login("codex", "pw",
        open_browser=lambda u: True, poll=lambda s, pw, base=None: "pending",
        sleep=lambda s: None, out=lambda m: None, attempts=3)
    assert ok is False
