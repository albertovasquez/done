"""Test that harness.proxy module exists and vibeproxy is a working alias."""


def test_proxy_module_exists_and_has_seam():
    from harness import proxy
    assert hasattr(proxy, "base_url")
    assert hasattr(proxy, "model_kwargs")
    assert hasattr(proxy, "model_value")


def test_vibeproxy_alias_is_same_module():
    from harness import proxy, vibeproxy
    assert vibeproxy.default_model is proxy.default_model
