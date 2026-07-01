from harness.goal_reviewer import review_goal
from harness.goal_gate import Verdict


def test_parses_met_yes():
    v = review_goal("g", "did the work", "m", caller=lambda p: "met: yes\nlooks done")
    assert isinstance(v, Verdict) and v.met is True


def test_parses_met_no_with_reason():
    v = review_goal("g", "t", "m", caller=lambda p: "met: no\ntests still red")
    assert v.met is False
    assert "tests still red" in v.reason


def test_unparseable_defaults_to_not_met():
    v = review_goal("g", "t", "m", caller=lambda p: "banana")
    assert v.met is False


def test_prompt_contains_goal_and_transcript():
    seen = {}
    def cap(p): seen["p"] = p; return "met: yes"
    review_goal("SHIP IT", "the transcript body", "m", caller=cap)
    assert "SHIP IT" in seen["p"] and "the transcript body" in seen["p"]
