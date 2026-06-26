import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import json
from harness.router import Router, Classification

_CATALOG = [
    ("poker-domain-rules", "Poker rake/rakeback math and PPPoker domain logic"),
    ("python-testing", "Write and run pytest unit/integration tests"),
]


def _stub(payload: str):
    """A complete_fn that ignores its args and returns a fixed string."""
    return lambda system, user: payload


def test_1_parses_validates_skills_and_unknown_type():
    r = Router(_stub(json.dumps({
        "task_type": "code_fix",
        "skills": ["poker-domain-rules", "not-a-real-skill"],
        "confidence": 0.9, "reasoning": "x", "suggested_model": None,
    })), catalog=_CATALOG, confidence_threshold=0.6)
    c = r.classify("fix the rakeback test")
    assert c.task_type == "code_fix"
    assert c.skills == ["poker-domain-rules"]      # hallucinated dropped
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


def test_3_unparseable_and_fenced_json():
    # (a) garbage -> safe ambiguous, no raise
    c = Router(_stub("I cannot help with that, here's some prose."),
               catalog=_CATALOG).classify("x")
    assert c.task_type == "ambiguous"
    assert c.confidence == 0.0
    assert c.needs_clarification is True

    # (b) fenced JSON -> parsed
    fenced = "```json\n" + json.dumps({"task_type": "ops_task", "skills": [],
                                       "confidence": 0.9, "reasoning": "pr"}) + "\n```"
    c2 = Router(_stub(fenced), catalog=_CATALOG).classify("make a PR")
    assert c2.task_type == "ops_task"
    assert c2.needs_clarification is False


def test_4_malformed_field_types_are_handled(tmp_path=None):
    # skills as a scalar string -> treated as empty (not character-mangled)
    c = Router(_stub(json.dumps({"task_type": "code_fix", "skills": "poker-domain-rules",
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
