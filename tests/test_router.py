import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import json
from trace.router import Router, Classification, SKILL_CATALOG


def _stub(payload: str):
    """A complete_fn that ignores its args and returns a fixed string."""
    return lambda system, user: payload


def test_1_parses_validates_skills_and_unknown_type():
    r = Router(_stub(json.dumps({
        "task_type": "code_fix",
        "skills": ["poker-domain-rules", "not-a-real-skill"],
        "confidence": 0.9, "reasoning": "x", "suggested_model": None,
    })), confidence_threshold=0.6)
    c = r.classify("fix the rakeback test")
    assert c.task_type == "code_fix"
    assert c.skills == ["poker-domain-rules"]      # hallucinated dropped
    assert c.needs_clarification is False

    r2 = Router(_stub(json.dumps({"task_type": "frobnicate", "skills": [],
                                  "confidence": 0.9, "reasoning": "x"})))
    c2 = r2.classify("weird")
    assert c2.task_type == "ambiguous"             # unknown normalized
    assert c2.needs_clarification is True


def test_2_low_confidence_and_ambiguous_set_gate():
    r = Router(_stub(json.dumps({"task_type": "code_fix", "skills": [],
                                 "confidence": 0.2, "reasoning": "unsure"})))
    c = r.classify("the tests are red")
    assert c.needs_clarification is True
    assert c.clarifying_question

    r2 = Router(_stub(json.dumps({"task_type": "ambiguous", "skills": [],
                                  "confidence": 0.95, "reasoning": "vague"})))
    assert r2.classify("do the thing").needs_clarification is True


def test_3_unparseable_and_fenced_json():
    # (a) garbage -> safe ambiguous, no raise
    c = Router(_stub("I cannot help with that, here's some prose.")).classify("x")
    assert c.task_type == "ambiguous"
    assert c.confidence == 0.0
    assert c.needs_clarification is True

    # (b) fenced JSON -> parsed
    fenced = "```json\n" + json.dumps({"task_type": "ops_task", "skills": [],
                                       "confidence": 0.9, "reasoning": "pr"}) + "\n```"
    c2 = Router(_stub(fenced)).classify("make a PR")
    assert c2.task_type == "ops_task"
    assert c2.needs_clarification is False


def test_4_malformed_field_types_are_handled(tmp_path=None):
    # skills as a scalar string -> treated as empty (not character-mangled)
    c = Router(_stub(json.dumps({"task_type": "code_fix", "skills": "poker-domain-rules",
                                 "confidence": 0.9, "reasoning": "x"}))).classify("fix")
    assert c.skills == []
    # reasoning null -> clarifying question must NOT contain the literal "None"
    c2 = Router(_stub(json.dumps({"task_type": "ambiguous", "skills": [],
                                  "confidence": 0.2, "reasoning": None}))).classify("huh")
    assert c2.needs_clarification is True
    assert "None" not in (c2.clarifying_question or "")
