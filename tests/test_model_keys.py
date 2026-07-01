from harness import model_keys


def test_api_key_provider_present_from_env():
    ks = model_keys.keys_present(
        auth_status={}, environ={"NEURALWATT_API_KEY": "x"})
    assert ks["neuralwatt"] is True


def test_api_key_provider_absent_when_env_missing():
    ks = model_keys.keys_present(auth_status={}, environ={})
    assert ks["neuralwatt"] is False


def test_oauth_provider_present_from_auth_status():
    ks = model_keys.keys_present(
        auth_status={"anthropic": {"status": "authenticated"}}, environ={})
    assert ks["anthropic"] is True
    # a provider absent from auth_status is False, not missing
    assert ks.get("codex", False) is False
