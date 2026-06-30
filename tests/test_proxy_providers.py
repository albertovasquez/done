from harness.proxy_service import providers


def test_mechanism_split_matches_verified_facts():
    by_id = {p.id: p for p in providers.PROVIDERS}
    assert by_id["anthropic"].mechanism == "browser_poll"
    assert by_id["codex"].mechanism == "browser_poll"
    assert by_id["antigravity"].mechanism == "browser_poll"
    assert by_id["xai"].mechanism == "cli_flag" and by_id["xai"].login_flag == "--xai-login"
    assert by_id["kimi"].mechanism == "cli_flag" and by_id["kimi"].login_flag == "--kimi-login"
    assert by_id["gemini"].mechanism == "api_key"
