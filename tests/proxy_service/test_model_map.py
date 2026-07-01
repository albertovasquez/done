from harness.proxy_service import model_map


def test_pairs_and_maps_are_consistent():
    # No aliases: proxy id == upstream id (identity pairs).
    pairs = model_map.NEURALWATT_MODELS
    assert ("glm-5.2", "glm-5.2") in pairs
    assert ("qwen3.5-397b-fast", "qwen3.5-397b-fast") in pairs
    assert ("glm-5.2-short-fast", "glm-5.2-short-fast") in pairs
    assert model_map.alias_to_upstream()["glm-5.2"] == "glm-5.2"
    assert model_map.upstream_to_alias()["glm-5.2"] == "glm-5.2"


def test_config_gen_reexports_same_pairs():
    # config_gen must consume the leaf, not define its own copy
    from harness.proxy_service import config_gen
    assert config_gen._NEURALWATT_MODELS == model_map.NEURALWATT_MODELS
