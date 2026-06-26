# tests/test_streaming_model.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import types
import pytest

from harness.streaming_model import StreamingLitellmModel, _extract_delta


def _chunk(content):
    """A minimal litellm-style stream chunk carrying delta.content."""
    delta = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(delta=delta)
    return types.SimpleNamespace(choices=[choice])


def _make_model(on_delta=None):
    # api_base/api_key live in model_kwargs per LitellmModelConfig (no top-level fields).
    return StreamingLitellmModel(
        on_delta=on_delta,
        model_name="openai/fake",
        model_kwargs={"api_base": "http://localhost:1/v1", "api_key": "x"},
        cost_tracking="ignore_errors",
    )


def test_extract_delta_returns_piece_or_empty():
    assert _extract_delta(_chunk("hi")) == "hi"
    assert _extract_delta(_chunk(None)) == ""
    assert _extract_delta(types.SimpleNamespace(choices=[])) == ""


def test_query_streams_each_prose_token_in_order(monkeypatch):
    seen = []
    pieces = ["Hel", "lo ", "world"]
    rebuilt_sentinel = object()

    def fake_completion(**kwargs):
        assert kwargs["stream"] is True
        return iter([_chunk(p) for p in pieces])

    def fake_builder(chunks, **kwargs):
        assert len(chunks) == len(pieces)
        return rebuilt_sentinel

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", fake_builder)

    model = _make_model(on_delta=seen.append)
    result = model._query([{"role": "user", "content": "x"}])
    assert seen == pieces                      # one callback per prose token, in order
    assert result is rebuilt_sentinel          # returns the rebuilt full response


def test_query_with_no_callback_takes_blocking_path(monkeypatch):
    called = {"stream": False, "blocking": False}

    def fake_completion(**kwargs):
        called["stream"] = kwargs.get("stream", False)
        # mimic a non-stream ModelResponse enough for the blocking path to return it
        return "BLOCKING_RESPONSE"

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = _make_model(on_delta=None)
    result = model._query([{"role": "user", "content": "x"}])
    assert called["stream"] is False           # blocking branch: stream not requested
    assert result == "BLOCKING_RESPONSE"
