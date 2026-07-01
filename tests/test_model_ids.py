from harness import model_ids


def test_alias_normalizes_to_upstream():
    # neuralwatt alias <-> models.dev upstream id
    assert model_ids.canonical("glm") == model_ids.canonical("glm-5.2")
    assert model_ids.canonical("qwen") == model_ids.canonical("qwen3.5-397b-fast")
    assert model_ids.canonical("glm-fast") == model_ids.canonical("glm-5.2-short-fast")


def test_strips_only_strict_date_suffix():
    assert model_ids.canonical("claude-haiku-4-5-20251001") == model_ids.canonical("claude-haiku-4-5")
    assert model_ids.canonical("claude-opus-4-20250514") == "claude-opus-4"


def test_no_overstrip_on_versioned_ids():
    # these must NOT be altered (no 8-digit date tail)
    for mid in ["claude-opus-4-6", "gpt-5.4", "gpt-5.4-mini", "claude-sonnet-5", "gpt-image-1.5"]:
        assert model_ids.canonical(mid) == mid


def test_matches_uses_canonical():
    assert model_ids.matches("glm", "glm-5.2")
    assert model_ids.matches("claude-haiku-4-5-20251001", "claude-haiku-4-5")
    assert not model_ids.matches("glm", "qwen")
