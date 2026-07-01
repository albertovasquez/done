from harness.proxy_service import model_map


def test_pairs_and_maps_are_consistent():
    pairs = model_map.NEURALWATT_MODELS
    assert ("glm-5.2", "glm") in pairs
    assert ("qwen3.5-397b-fast", "qwen") in pairs
    assert ("glm-5.2-short-fast", "glm-fast") in pairs
    assert model_map.alias_to_upstream()["glm"] == "glm-5.2"
    assert model_map.upstream_to_alias()["glm-5.2"] == "glm"


def test_config_gen_reexports_same_pairs():
    # config_gen must consume the leaf, not define its own copy
    from harness.proxy_service import config_gen
    assert config_gen._NEURALWATT_MODELS == model_map.NEURALWATT_MODELS
