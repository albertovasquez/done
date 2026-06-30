from harness.proxy_service import login


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
