"""Unit tests for ChatHandler.answer_stream — the streaming chat path.

Mock mode (no worker model) yields exactly one honest piece; real mode passes
stream=True to litellm and yields the non-empty content deltas in order. No live
proxy: litellm.completion is monkeypatched.
"""



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
from harness.skills import SkillMeta  # noqa: E402

CAT = [
    SkillMeta("test-driven-development", "Write a failing test first, then minimal code."),
    SkillMeta("systematic-debugging", "Find the root cause before fixing."),
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


def test_catalog_answer_groups_by_origin_hides_bundled_and_skips_empty():
    # The user-facing answer groups visible skills by origin (global/user/project),
    # hides bundled (the curated spine), and omits empty groups.
    cat = [
        SkillMeta("hidden-spine", "internal", origin="bundled"),
        SkillMeta("caveman", "compress speech", origin="global"),
        SkillMeta("repo-skill", "project-local", origin="project"),
    ]
    out = "".join(ChatHandler(None, catalog=cat).answer_stream(
        "list skills by origin, global or user or project"))
    # grouped headers for the non-empty origins, in fixed order (global before project)
    assert "### Global skills" in out and "### Project skills" in out
    assert out.index("### Global skills") < out.index("### Project skills")
    # the empty 'user' group is omitted
    assert "### User skills" not in out
    # bundled is hidden: neither its header nor its skill name appears
    assert "hidden-spine" not in out
    # visible skills render under their group
    assert "caveman" in out and "repo-skill" in out


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


def test_persona_block_prepended_as_system_message(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("ok")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    list(ChatHandler("gpt-5.4", persona_block="BE TERSE").answer_stream("hi"))
    assert captured["messages"] == [
        {"role": "system", "content": "BE TERSE"},
        {"role": "user", "content": "hi"},
    ]


def test_persona_block_prepended_before_history(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("ok")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    history = [{"role": "user", "content": "earlier"}]
    list(ChatHandler("gpt-5.4", persona_block="BE TERSE").answer_stream("hi", history=history))
    assert captured["messages"] == [
        {"role": "system", "content": "BE TERSE"},
        {"role": "user", "content": "earlier"},
        {"role": "user", "content": "hi"},
    ]


def test_empty_persona_block_adds_no_system_message(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("ok")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    list(ChatHandler("gpt-5.4", persona_block="").answer_stream("hi"))
    assert captured["messages"] == [{"role": "user", "content": "hi"}]   # byte-identical


def test_base_block_becomes_system_message_before_persona(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        # minimal streaming-shaped stub: yield nothing
        return iter(())

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    h = ChatHandler("vibeproxy", catalog=[], persona_block="PERSONA",
                    base_block="BASEBLOCK")
    list(h.answer_stream("hi"))  # drives litellm.completion with our messages

    sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
    assert sys_msgs, "chat path must have a system message when base_block is set"
    content = sys_msgs[0]["content"]
    assert content.index("BASEBLOCK") < content.index("PERSONA")
