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


# --- deterministic skills-list path (flag-gated, OFF by default) -------------
# HARNESS_DETERMINISTIC_SKILLS_LIST=1 enables the legacy catalog-dump path. The
# tests below opt in explicitly; the default-OFF behavior is covered after.
FLAG = "HARNESS_DETERMINISTIC_SKILLS_LIST"


def test_capability_question_answered_from_catalog_when_flag_on(monkeypatch):
    # With the flag ON, routing already knows the catalog, so we answer a
    # capability question deterministically WITHOUT a model (works in mock mode).
    monkeypatch.setenv(FLAG, "1")
    out = "".join(ChatHandler(None, catalog=CAT).answer_stream("what skills do we have?"))
    assert "test-driven-development" in out
    assert "Write a failing test first" in out
    assert "systematic-debugging" in out
    assert MOCK_LINE not in out                 # did NOT fall through to the mock line


def test_capability_question_does_not_call_the_model_when_flag_on(monkeypatch):
    # With the flag ON, even with a real model id, a capability question is
    # answered from the catalog and must NOT hit litellm.
    monkeypatch.setenv(FLAG, "1")
    import litellm

    def boom(**kwargs):
        raise AssertionError("model must not be called for a capability question")

    monkeypatch.setattr(litellm, "completion", boom)
    out = "".join(ChatHandler("gpt-5.4", catalog=CAT).answer_stream("what skills do you have?"))
    assert "test-driven-development" in out


def test_capability_question_with_empty_catalog_says_none_when_flag_on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    out = "".join(ChatHandler(None, catalog=[]).answer_stream("what skills do we have?"))
    assert "no skills" in out.lower()


def test_capability_question_goes_to_model_by_default(monkeypatch):
    # Default (flag unset): a skill question is NOT hijacked into a list dump —
    # it goes to the model, which has the skills menu in its context and can
    # answer the actual question (e.g. "are descriptions sent to context?").
    monkeypatch.delenv(FLAG, raising=False)
    captured = {}
    import litellm

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("answer")])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    out = "".join(ChatHandler("gpt-5.4", catalog=CAT).answer_stream(
        "are all these skills descriptions sent to the llm in context?"))
    assert captured                              # the model WAS called
    assert "test-driven-development" not in out  # NOT a deterministic catalog dump
    assert out == "answer"


def test_skill_question_in_mock_mode_is_honest_by_default(monkeypatch):
    # Default + mock mode: a skill question yields the honest mock line, not a
    # list dump (we can't answer without a model, and won't pretend to).
    monkeypatch.delenv(FLAG, raising=False)
    out = "".join(ChatHandler(None, catalog=CAT).answer_stream("what skills do we have?"))
    assert out == MOCK_LINE


def test_catalog_answer_groups_by_origin_hides_bundled_and_skips_empty(monkeypatch):
    # The deterministic answer (flag ON) groups visible skills by origin
    # (global/user/project), hides bundled (the curated spine), omits empty groups.
    monkeypatch.setenv(FLAG, "1")
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


# ---- tools questions: answered from the live registry, not the model ---------

def test_is_capability_question_matches_possessive_tool_questions():
    for q in ["what tools do you have access to?", "what tools do you have",
              "your tools", "tools you can use", "what commands do you have",
              "which tools do you have"]:
        assert is_capability_question(q), q


def test_is_capability_question_rejects_build_a_tool_requests():
    # Regression guard: a request to BUILD/USE a tool must fall through to the
    # model, not be hijacked as a capability question.
    for q in ["write a tool to parse logs", "what tools should I use in Rust?",
              "build me a CLI tool", "this command failed, why?"]:
        assert not is_capability_question(q), q


def test_tools_question_lists_the_real_registry_tools_in_mock_mode():
    # Answered deterministically from build_registry() — no model, works in mock.
    out = "".join(ChatHandler(None, catalog=CAT).answer_stream(
        "what tools do you have access to?"))
    for name in ("bash", "read", "write", "edit"):
        assert name in out, name
    assert MOCK_LINE not in out                 # did NOT fall through to the mock line


def test_tools_question_does_not_call_the_model(monkeypatch):
    import litellm

    def boom(**kwargs):
        raise AssertionError("model must not be called for a tools question")

    monkeypatch.setattr(litellm, "completion", boom)
    out = "".join(ChatHandler("gpt-5.4", catalog=CAT).answer_stream("your tools"))
    assert "bash" in out


def test_tools_question_also_lists_skills_and_plan(monkeypatch):
    # The fuller capability surface: tools + the loaded skill catalog + plan note.
    import litellm

    def boom(**kwargs):
        raise AssertionError("model must not be called")

    monkeypatch.setattr(litellm, "completion", boom)
    out = "".join(ChatHandler("gpt-5.4", catalog=CAT).answer_stream(
        "what tools do you have?"))
    assert "bash" in out                         # a tool
    assert "test-driven-development" in out       # a skill from the catalog
    assert "plan" in out.lower()                  # the checklist command note


def test_skills_dump_has_no_tools_section_when_flag_on(monkeypatch):
    # No-regression: the deterministic skills dump (flag ON) yields the catalog
    # answer with NO tools section bleeding in. With the flag OFF a skills
    # question goes to the model (covered by
    # test_capability_question_goes_to_model_by_default), so the deterministic
    # dump is only reachable — and only assertable — with the flag set.
    monkeypatch.setenv(FLAG, "1")
    import litellm

    def boom(**kwargs):
        raise AssertionError("model must not be called")

    monkeypatch.setattr(litellm, "completion", boom)
    out = "".join(ChatHandler("gpt-5.4", catalog=CAT).answer_stream(
        "what skills do we have?"))
    assert "test-driven-development" in out
    assert "bash" not in out                      # tools list must not appear here


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


def test_stalled_chat_call_is_interrupted_before_first_token(monkeypatch):
    """A chat completion() that blocks before yielding any chunk must abort on
    cancel_flag (Finding 1+2): the per-piece check can't reach a pre-first-token
    stall, so run_interruptible wraps the completion() open."""
    import threading
    import pytest
    from minisweagent.exceptions import UserInterruption

    flag = threading.Event()
    started = threading.Event()
    release = threading.Event()

    def fake_completion(**kwargs):
        started.set()
        release.wait(timeout=5)
        return iter([_Chunk("late")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    def canceller():
        started.wait(timeout=5)
        flag.set()

    threading.Thread(target=canceller, daemon=True).start()
    gen = ChatHandler("vibeproxy").answer_stream("hi", cancel_flag=flag)
    with pytest.raises(UserInterruption):
        list(gen)
    release.set()


def test_chat_cancel_between_pieces(monkeypatch):
    """Once tokens flow, a cancel_flag set mid-stream aborts on the next piece."""
    import threading
    import pytest
    from minisweagent.exceptions import UserInterruption

    flag = threading.Event()

    def fake_completion(**kwargs):
        return iter([_Chunk("a"), _Chunk("b"), _Chunk("c")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    out = []
    gen = ChatHandler("vibeproxy").answer_stream("hi", cancel_flag=flag)
    with pytest.raises(UserInterruption):
        for piece in gen:
            out.append(piece)
            flag.set()             # cancel after the first piece
    assert out == ["a"]            # stopped on the next iteration, no "b"/"c"


def test_chat_cancel_flag_none_streams_unchanged(monkeypatch):
    """cancel_flag=None (CLI/mock) => streaming unchanged."""
    def fake_completion(**kwargs):
        return iter([_Chunk("x"), _Chunk("y")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    assert list(ChatHandler("vibeproxy").answer_stream("hi")) == ["x", "y"]


# ---- wants_tool: the throwaway tools-probe (chat-path-tools) ----

_BASH_SCHEMA = {"type": "function", "function": {"name": "bash", "parameters": {}}}


def test_wants_tool_mock_mode_returns_false_no_call(monkeypatch):
    """model_id=None (mock) must return False WITHOUT calling litellm."""
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("litellm.completion must not be called in mock mode")

    import litellm
    monkeypatch.setattr(litellm, "completion", _boom)

    handler = ChatHandler(None, tool_schemas=[_BASH_SCHEMA])
    assert handler.wants_tool("what's my setup?") is False
    assert called["n"] == 0


def test_wants_tool_no_schemas_returns_false_no_call(monkeypatch):
    """A real model but no tool_schemas => False, no litellm call."""
    import litellm
    monkeypatch.setattr(litellm, "completion",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not call without schemas")))
    assert ChatHandler("glm-5.2").wants_tool("inspect") is False


class _FakeMsg:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, tool_calls):
        self.message = _FakeMsg(tool_calls)


class _FakeResp:
    def __init__(self, tool_calls):
        self.choices = [_FakeChoice(tool_calls)]


def _handler_with_model():
    return ChatHandler("glm-5.2", persona_block="", base_block="You are an agent.",
                       tool_schemas=[_BASH_SCHEMA])


def test_wants_tool_true_when_tool_calls_present(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "completion",
                        lambda *a, **k: _FakeResp([{"id": "tc1"}]))
    assert _handler_with_model().wants_tool("inspect my setup") is True


def test_wants_tool_false_when_no_tool_calls(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "completion", lambda *a, **k: _FakeResp(None))
    assert _handler_with_model().wants_tool("how are you?") is False


def test_wants_tool_fail_open_on_exception(monkeypatch):
    import litellm

    def _raise(*a, **k):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(litellm, "completion", _raise)
    assert _handler_with_model().wants_tool("anything") is False
