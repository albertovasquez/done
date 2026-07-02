from harness.tracing_agent import _usage_from_extra


def _extra(usage):
    return {"response": {"usage": usage}}


def test_openai_shape_extracts_cached_tokens():
    out = _usage_from_extra(_extra({
        "total_tokens": 100, "prompt_tokens": 80, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 64},
    }))
    assert out == {"total": 100, "prompt": 80, "completion": 20, "cached": 64}


def test_anthropic_shape_extracts_cache_read_tokens():
    out = _usage_from_extra(_extra({
        "input_tokens": 80, "output_tokens": 20,
        "cache_read_input_tokens": 48,
    }))
    assert out["cached"] == 48


def test_no_cache_fields_omits_cached_key():
    out = _usage_from_extra(_extra({
        "total_tokens": 100, "prompt_tokens": 80, "completion_tokens": 20}))
    assert "cached" not in out


def test_non_int_cached_ignored():
    out = _usage_from_extra(_extra({
        "prompt_tokens": 80, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": None},
    }))
    assert "cached" not in out
