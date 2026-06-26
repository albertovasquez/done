"""Unit tests for ChatHandler.answer_stream — the streaming chat path.

Mock mode (no worker model) yields exactly one honest piece; real mode passes
stream=True to litellm and yields the non-empty content deltas in order. No live
proxy: litellm.completion is monkeypatched.
"""

import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.chat_handler import ChatHandler

MOCK_LINE = ("[mock mode] classified as chat_question; chat answers require "
             "--model vibeproxy. (Routing worked: this did not run the agent.)")


def test_mock_mode_yields_single_honest_piece():
    pieces = list(ChatHandler(None).answer_stream("what is python"))
    assert pieces == [MOCK_LINE]


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)]


def test_real_mode_streams_pieces_in_order_with_stream_true(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        # a chunk with None content (e.g. role-only first chunk) must be skipped
        return iter([_Chunk("Hello"), _Chunk(" "), _Chunk(None), _Chunk("world")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    pieces = list(ChatHandler("gpt-5.4").answer_stream("hi"))

    assert pieces == ["Hello", " ", "world"]      # None skipped, order preserved
    assert captured.get("stream") is True
    assert captured.get("model") == "openai/gpt-5.4"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


def test_history_is_prepended_to_messages(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("ok")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    history = [{"role": "user", "content": "earlier q"},
               {"role": "assistant", "content": "earlier a"}]
    list(ChatHandler("gpt-5.4").answer_stream("follow up", history=history))

    assert captured["messages"] == [
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": "earlier a"},
        {"role": "user", "content": "follow up"},
    ]
