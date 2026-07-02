"""Digest cap: a worker's returned body enters the ORCHESTRATOR's context
verbatim (subagent.py:_format_digest → tool output → parent transcript). Nothing
bounds its size — the worker's own summary contract is the only brake. This pins
a defensive per-body char cap so a single runaway worker can't dump an unbounded
blob into the parent, while leaving normal (short) summaries byte-for-byte intact.
"""
from harness.tools.subagent import _format_digest, MAX_BODY_CHARS


def test_short_body_is_untouched():
    # A normal, well-behaved summary must pass through with NO truncation marker.
    body = "Found the bug in foo.py:42; fixed off-by-one. No files added."
    out = _format_digest([(True, body)], ["find the bug"])
    assert body in out
    assert "truncated" not in out


def test_runaway_body_is_capped_with_marker():
    huge = "x" * (MAX_BODY_CHARS + 5000)
    out = _format_digest([(True, huge)], ["dump everything"])
    # The kept body is bounded to the cap...
    assert out.count("x") <= MAX_BODY_CHARS
    # ...and truncation is SIGNALLED (silent truncation reads as "complete").
    assert "truncated" in out
    assert "5000" in out  # the dropped char count is surfaced


def test_cap_is_per_worker_not_per_digest():
    # Two runaway workers each get their own budget — the cap is per body,
    # so one verbose sibling doesn't starve another.
    huge = "y" * (MAX_BODY_CHARS + 100)
    out = _format_digest([(True, huge), (True, huge)], ["a", "b"])
    assert out.count("y") <= 2 * MAX_BODY_CHARS
    assert out.count("truncated") == 2


def test_failure_bodies_are_capped_too():
    # A failing worker's error text is also parent-context bytes; cap it as well.
    huge = "z" * (MAX_BODY_CHARS + 2000)
    out = _format_digest([(False, huge)], ["boom"])
    assert out.count("z") <= MAX_BODY_CHARS
    assert "truncated" in out


def test_header_and_goal_never_truncated():
    # The header/goal are the orchestrator's OWN controlled strings — a long goal
    # is not worker output and must survive intact.
    long_goal = "g" * (MAX_BODY_CHARS + 50)
    out = _format_digest([(True, "ok")], [long_goal])
    assert long_goal in out
