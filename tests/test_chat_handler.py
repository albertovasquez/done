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


# ---- capability questions: answered from the catalog, not the model ----------

from harness.chat_handler import is_capability_question  # noqa: E402

CAT = [
    ("test-driven-development", "Write a failing test first, then minimal code."),
    ("systematic-debugging", "Find the root cause before fixing."),
]


def test_is_capability_question_matches_skill_meta_questions():
    for q in ["what skills do we have?", "which skills do you have",
              "list your skills", "what skills are available", "what can you do?",
              "what are your capabilities"]:
        assert is_capability_question(q), q


def test_is_capability_question_rejects_ordinary_chat():
    for q in ["what is python?", "use TDD to add validation",
              "debug this skillfully", "explain how the router works"]:
        assert not is_capability_question(q), q


def test_capability_question_answered_from_catalog_even_in_mock_mode():
    # The whole point: routing already knows the catalog, so we can answer a
    # capability question deterministically WITHOUT a model (works in mock mode).
    out = "".join(ChatHandler(None, catalog=CAT).answer_stream("what skills do we have?"))
    assert "test-driven-development" in out
    assert "Write a failing test first" in out
    assert "systematic-debugging" in out
    assert MOCK_LINE not in out                 # did NOT fall through to the mock line


def test_capability_question_does_not_call_the_model(monkeypatch):
    # Even with a real model id, a capability question must be answered from the
    # catalog and must NOT hit litellm.
    import litellm

    def boom(**kwargs):
        raise AssertionError("model must not be called for a capability question")

    monkeypatch.setattr(litellm, "completion", boom)
    out = "".join(ChatHandler("gpt-5.4", catalog=CAT).answer_stream("what skills do you have?"))
    assert "test-driven-development" in out


def test_capability_question_with_empty_catalog_says_none():
    out = "".join(ChatHandler(None, catalog=[]).answer_stream("what skills do we have?"))
    assert "no skills" in out.lower()


def test_ordinary_chat_still_streams_from_model_when_catalog_present(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("Py"), _Chunk("thon")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    out = "".join(ChatHandler("gpt-5.4", catalog=CAT).answer_stream("what is python?"))
    assert out == "Python"                       # catalog present but not a meta-question
    assert captured.get("stream") is True
