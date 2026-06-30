import json
from harness.router import Router, Classification
from harness.skills import SkillMeta

_CATALOG = [
    SkillMeta("systematic-debugging", "Use when there is a REPORTED failing behavior to fix — a bug, failing test, or error. NOT for read-only status checks."),
    SkillMeta("test-driven-development", "Use when implementing any feature or bugfix, before writing implementation code"),
]


def _stub(payload: str):
    """A complete_fn that ignores its args and returns a fixed string."""
    return lambda system, user: payload


def test_classify_includes_preamble_in_user_message():
    seen = {}

    def stub(system, user):
        seen["user"] = user
        return '{"task_type": "code_fix", "skills": [], "confidence": 0.9, "reasoning": "x"}'

    history = [{"role": "user", "content": "earlier ask", "origin": "chat"},
               {"role": "assistant", "content": "chat reply", "origin": "chat"},
               {"role": "assistant", "content": "agent narration", "origin": "agent"}]
    Router(stub, catalog=_CATALOG).classify("the first one", history=history)
    assert "earlier ask" in seen["user"]
    assert "chat reply" in seen["user"]
    assert "agent narration" not in seen["user"]
    assert "the first one" in seen["user"]            # current prompt remains the target


def test_classify_without_history_passes_bare_prompt():
    seen = {}

    def stub(system, user):
        seen["user"] = user
        return '{"task_type": "code_fix", "skills": [], "confidence": 0.9, "reasoning": "x"}'

    Router(stub, catalog=_CATALOG).classify("just this", history=None)
    assert seen["user"] == "just this"                # byte-for-byte unchanged


def test_1_parses_validates_skills_and_unknown_type():
    r = Router(_stub(json.dumps({
        "task_type": "code_fix",
        "skills": ["systematic-debugging", "not-a-real-skill"],
        "confidence": 0.9, "reasoning": "x", "suggested_model": None,
    })), catalog=_CATALOG, confidence_threshold=0.6)
    c = r.classify("fix the rakeback test")
    assert c.task_type == "code_fix"
    assert c.skills == ["systematic-debugging"]    # hallucinated dropped
    assert c.needs_clarification is False

    r2 = Router(_stub(json.dumps({"task_type": "frobnicate", "skills": [],
                                  "confidence": 0.9, "reasoning": "x"})),
                catalog=_CATALOG)
    c2 = r2.classify("weird")
    assert c2.task_type == "ambiguous"             # unknown normalized
    assert c2.needs_clarification is True


def test_2_low_confidence_and_ambiguous_set_gate():
    r = Router(_stub(json.dumps({"task_type": "code_fix", "skills": [],
                                 "confidence": 0.2, "reasoning": "unsure"})),
               catalog=_CATALOG)
    c = r.classify("the tests are red")
    assert c.needs_clarification is True
    assert c.clarifying_question

    r2 = Router(_stub(json.dumps({"task_type": "ambiguous", "skills": [],
                                  "confidence": 0.95, "reasoning": "vague"})),
                catalog=_CATALOG)
    assert r2.classify("do the thing").needs_clarification is True


def test_3_unparseable_and_fenced_json(caplog):
    # (a) garbage -> DEGRADE TO WORKER (chat_question, no refusal), no raise — AND
    # a warning (so a garbage-spewing cheap model is diagnosable). The router is
    # best-effort triage: it must never hard-gate a turn just because the cheap
    # classifier returned non-JSON.
    with caplog.at_level("WARNING", logger="harness.router"):
        c = Router(_stub("I cannot help with that, here's some prose."),
                   catalog=_CATALOG).classify("x")
    assert c.task_type == "chat_question"          # → worker, NOT 'ambiguous'
    assert c.confidence == 0.0
    assert c.needs_clarification is False          # never refuses
    assert any("unparseable" in r.message for r in caplog.records), \
        f"unparseable router output must warn; got {[r.message for r in caplog.records]}"

    # (b) fenced JSON -> parsed
    fenced = "```json\n" + json.dumps({"task_type": "ops_task", "skills": [],
                                       "confidence": 0.9, "reasoning": "pr"}) + "\n```"
    c2 = Router(_stub(fenced), catalog=_CATALOG).classify("make a PR")
    assert c2.task_type == "ops_task"
    assert c2.needs_clarification is False


def test_4_malformed_field_types_are_handled(tmp_path=None):
    # skills as a scalar string -> treated as empty (not character-mangled)
    c = Router(_stub(json.dumps({"task_type": "code_fix", "skills": "systematic-debugging",
                                 "confidence": 0.9, "reasoning": "x"})),
               catalog=_CATALOG).classify("fix")
    assert c.skills == []
    # reasoning null -> clarifying question must NOT contain the literal "None"
    c2 = Router(_stub(json.dumps({"task_type": "ambiguous", "skills": [],
                                  "confidence": 0.2, "reasoning": None})),
                catalog=_CATALOG).classify("huh")
    assert c2.needs_clarification is True
    assert "None" not in (c2.clarifying_question or "")


def test_5_system_prompt_tells_router_it_runs_in_a_real_project():
    """The router must know the agent operates IN a project directory it can
    inspect, so questions about "this app/code/project" route to code_explain
    (let the agent look) instead of bouncing to ambiguous. We capture the system
    prompt the router actually sends."""
    seen = {}

    def capture(system, user):
        seen["system"] = system
        return json.dumps({"task_type": "code_explain", "skills": [],
                           "confidence": 0.9, "reasoning": "x"})

    Router(capture, catalog=_CATALOG).classify("What kind of application is this?")
    sys_l = seen["system"].lower()
    # the prompt names the working/project directory and that it can be inspected
    assert "project" in sys_l or "working directory" in sys_l, seen["system"]
    assert "inspect" in sys_l or "read" in sys_l, seen["system"]
    # and explicitly steers project-reference questions to code_explain, not ambiguous
    assert "code_explain" in seen["system"], seen["system"]


# ---- structured clarification options (#66) ----

def test_classify_parses_options_array():
    payload = ('{"task_type": "ambiguous", "confidence": 0.2, "reasoning": "vague", '
               '"options": [{"title": "Explain how auth works", "rationale": "read the code"}, '
               '{"title": "Fix the auth bug", "rationale": "repair the failing check"}]}')
    cls = Router(_stub(payload), catalog=_CATALOG).classify("do the auth thing")
    assert cls.needs_clarification
    assert cls.options == [("Explain how auth works", "read the code"),
                           ("Fix the auth bug", "repair the failing check")]


def test_classify_options_absent_degrades_to_empty_and_keeps_question():
    payload = '{"task_type": "ambiguous", "confidence": 0.1, "reasoning": "unclear"}'
    cls = Router(_stub(payload), catalog=_CATALOG).classify("hmm")
    assert cls.options == []
    assert cls.clarifying_question                      # flat question still set


def test_classify_malformed_options_filtered_no_raise():
    # scalar options, an entry missing title, and a non-dict entry — all dropped
    payload = ('{"task_type": "ambiguous", "confidence": 0.1, "reasoning": "x", '
               '"options": [{"rationale": "no title"}, "junk", {"title": "Keep me", "rationale": "ok"}]}')
    cls = Router(_stub(payload), catalog=_CATALOG).classify("hmm")
    assert cls.options == [("Keep me", "ok")]


def test_classify_clear_request_has_no_options():
    payload = '{"task_type": "code_fix", "skills": [], "confidence": 0.95, "reasoning": "clear"}'
    cls = Router(_stub(payload), catalog=_CATALOG).classify("fix the add() bug")
    assert cls.options == []
    assert not cls.needs_clarification


def test_router_drops_non_model_invocable_selection():
    # A disable-model-invocation skill must never be auto-selected, even if the
    # cheap model names it in the JSON.
    cat = [SkillMeta("a", "desc a", model_invocable=True),
           SkillMeta("deploy", "desc deploy", model_invocable=False)]
    r = Router(_stub(json.dumps({"task_type": "code_fix",
               "skills": ["a", "deploy"], "confidence": 0.9,
               "reasoning": "x", "suggested_model": None})), catalog=cat)
    cls = r.classify("do it")
    assert cls.skills == ["a"]            # 'deploy' is dormant -> dropped


def test_router_prompt_omits_dormant_skill():
    cat = [SkillMeta("visible", "v desc", model_invocable=True),
           SkillMeta("hidden", "h desc", model_invocable=False)]
    captured = {}
    def stub(system, user):
        captured["system"] = system
        return json.dumps({"task_type": "code_fix", "skills": [], "confidence": 0.9,
                           "reasoning": "x", "suggested_model": None})
    Router(stub, catalog=cat).classify("x")
    assert "visible" in captured["system"] and "hidden" not in captured["system"]


def test_system_prompt_teaches_observe_vs_fix():
    """The router prompt must steer observe-intent ('check', 'is X working') away
    from debugging skills, so read-only requests don't pull in systematic-debugging
    (#177). Advisory text check, not a model-output check."""
    from harness.router import _system_prompt
    prompt = _system_prompt([]).lower()
    assert "observe" in prompt
    assert "check" in prompt
    assert "do not" in prompt or "don't" in prompt


def test_stub_complete_classifies_command_regardless_of_history():
    """The offline test stub (HARNESS_ROUTER_STUB) must classify a COMMAND the
    same way whether or not prior turns exist. Router.classify wraps the prompt
    with a "Recent context ... Classify THIS request: <prompt>" preamble when
    history is present; a naive first-word heuristic would then see "Recent" and
    mis-route every follow-up command to chat. The stub reads the request after
    the marker, so the classification is history-independent."""
    from harness.acp_main import _stub_complete
    r = Router(_stub_complete, catalog=[])
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    # A command classifies as code_fix with AND without history.
    assert r.classify("fix the bug").task_type == "code_fix"
    assert r.classify("fix the bug", history=hist).task_type == "code_fix"
    # A question stays chat_question with AND without history.
    assert r.classify("what is 1+1").task_type == "chat_question"
    assert r.classify("what is 1+1", history=hist).task_type == "chat_question"


def test_system_prompt_routes_greetings_to_chat_not_ambiguous():
    """A greeting must be classified chat_question (→ the active persona answers
    in character), never 'ambiguous' (which strips personalization). Regression
    for the soulless "I couldn't interpret that" reply to "hi"."""
    from harness.router import _system_prompt
    p = _system_prompt([]).lower()
    # the prompt must steer greetings to chat_question and explicitly NOT ambiguous
    assert "greeting" in p
    assert "chat_question" in p
    assert "persona" in p                         # persona-neutral (not "Bob")
    # and it must NOT name a specific persona — the router is persona-agnostic
    assert "bob" not in p


def test_unparseable_classification_degrades_to_worker_not_refusal():
    """The router is best-effort: when the cheap model returns non-JSON (a chatty
    fallback, a cooling-down provider), DON'T refuse — route to chat_question so
    the active persona handles it. Regression for the 'I couldn't interpret that'
    storm when the router fallback model misbehaves."""
    r = Router(_stub("Hi! I'm an assistant. How can I help?"), catalog=_CATALOG)
    c = r.classify("Hi there")
    assert c.task_type == "chat_question"          # → worker, not 'ambiguous'
    assert c.needs_clarification is False          # never refuses
    assert "interpret" not in (c.clarifying_question or "").lower()


def test_unparseable_partial_json_also_degrades_to_worker():
    r = Router(_stub("garbage { not really json"), catalog=_CATALOG)
    c = r.classify("fix the bug")
    assert c.task_type == "chat_question"
    assert c.needs_clarification is False
