from harness.goal_gate import decide, Verdict, GateLimits


L = GateLimits(max_attempts=3)


def test_no_goal_stops():
    d = decide(goal=None, verdict=None, reviewer_attempts=0, limits=L)
    assert d.action == "stop"


def test_met_stops():
    d = decide(goal="g", verdict=Verdict(met=True), reviewer_attempts=1, limits=L)
    assert d.action == "stop"


def test_unmet_continues_with_reason():
    d = decide(goal="g", verdict=Verdict(met=False, reason="tests red"),
               reviewer_attempts=1, limits=L)
    assert d.action == "continue"
    assert "tests red" in d.reason


def test_reviewer_failure_escapes():
    d = decide(goal="g", verdict=None, reviewer_attempts=1, limits=L)
    assert d.action == "escape"


def test_budget_exhausted_escapes():
    d = decide(goal="g", verdict=Verdict(met=False), reviewer_attempts=3, limits=L)
    assert d.action == "escape"
