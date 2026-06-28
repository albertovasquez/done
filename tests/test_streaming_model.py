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


def test_on_delta_exception_aborts_the_stream(monkeypatch):
    """An exception raised by on_delta must abort the stream immediately — this is
    the mechanism ESC uses to kill an in-flight LLM call. The loop must NOT keep
    pulling chunks after the callback raises."""
    pulled = []

    def fake_completion(**kwargs):
        def gen():
            for p in ["a", "b", "c"]:
                pulled.append(p)
                yield _chunk(p)
        return gen()

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    class Interrupt(Exception):
        pass

    def on_delta(piece):
        raise Interrupt()                      # fire on the very first prose piece

    model = _make_model(on_delta=on_delta)
    with pytest.raises(Interrupt):
        model._query([{"role": "user", "content": "x"}])
    assert pulled == ["a"], "stream kept producing chunks after on_delta raised"


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


def test_no_blocking_retry_after_a_delta_was_emitted(monkeypatch):
    """If reassembly returns None but deltas were already shown, do NOT retry —
    the user must not see one generation then have a different one committed."""
    seen = []
    blocking_calls = {"n": 0}

    def fake_completion(**kwargs):
        if kwargs.get("stream"):
            return iter([_chunk("partial")])   # one delta emitted
        blocking_calls["n"] += 1               # a retry would land here
        return "RETRY_RESPONSE"

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", lambda chunks, **kw: None)

    model = _make_model(on_delta=seen.append)
    result = model._query([{"role": "user", "content": "x"}])
    assert seen == ["partial"]                 # the user saw the streamed text
    assert blocking_calls["n"] == 0            # and we did NOT retry
    assert result is None                       # None propagates (treated as failure upstream)


def test_blocking_fallback_only_when_zero_deltas(monkeypatch):
    """Reassembly None AND no deltas emitted → one safe blocking fallback."""
    def fake_completion(**kwargs):
        if kwargs.get("stream"):
            return iter([])                    # empty stream, zero deltas
        return "FALLBACK"

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", lambda chunks, **kw: None)

    model = _make_model(on_delta=lambda p: None)
    assert model._query([{"role": "user", "content": "x"}]) == "FALLBACK"


def test_reassembled_response_preserves_tool_calls(monkeypatch):
    """A streamed bash tool-call survives stream_chunk_builder so the inherited
    query() parses the same actions a blocking response would."""
    seen = []

    # Real litellm.stream_chunk_builder over real chunk objects is the safest
    # fidelity check; build chunks the same way litellm emits a tool call.
    from litellm.types.utils import (ModelResponseStream, StreamingChoices,
                                     Delta, ChatCompletionDeltaToolCall, Function)

    def tc_chunk(idx, name=None, args="", finish=None):
        tcs = None
        if name is not None or args:
            tcs = [ChatCompletionDeltaToolCall(
                index=0, id=("call_1" if name else None), type="function",
                function=Function(name=name, arguments=args))]
        return ModelResponseStream(choices=[StreamingChoices(
            index=0, delta=Delta(content=None, tool_calls=tcs), finish_reason=finish)])

    chunks = [
        tc_chunk(0, name="bash", args=""),
        tc_chunk(0, args='{"command": "ls"}'),
        tc_chunk(0, finish="tool_calls"),
    ]

    def fake_completion(**kwargs):
        return iter(chunks)

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = _make_model(on_delta=seen.append)
    rebuilt = model._query([{"role": "user", "content": "x"}])
    # the rebuilt response must carry the tool call so upstream parsing works
    tool_calls = rebuilt.choices[0].message.tool_calls
    assert tool_calls and tool_calls[0].function.name == "bash"
    assert '"command": "ls"' in tool_calls[0].function.arguments
    assert seen == []                          # tool-call deltas are not prose
