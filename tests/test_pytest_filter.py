from harness.output_filters.dispatch import filter_output
from harness.output_filters.pytest_filter import matches, filter_pytest

CLEAN = (
    "============================= test session starts =============================\n"
    "collected 42 items\n\n"
    "tests/test_a.py ......\n"
    "tests/test_b.py ....................\n"
    "tests/test_c.py ................\n\n"
    "============================== 42 passed in 1.23s ==============================\n"
)

FAILING = (
    "============================= test session starts =============================\n"
    "collected 3 items\n\n"
    "tests/test_x.py .F.\n\n"
    "=================================== FAILURES ===================================\n"
    "___________________________________ test_y ____________________________________\n"
    "    def test_y():\n"
    ">       assert 1 == 2\n"
    "E       assert 1 == 2\n\n"
    "tests/test_x.py:7: AssertionError\n"
    "=========================== 1 failed, 2 passed in 0.04s ===========================\n"
)


def test_matches_pytest_command():
    assert matches("pytest tests/ -q")
    assert matches("python -m pytest tests/test_a.py")
    assert matches("/usr/bin/pytest tests/")
    assert not matches("git status")
    # Minor #5: tighter matcher — these must NOT match
    assert not matches("cat pytest.ini")
    assert not matches("echo pytest")
    assert not matches("python pytest_helper.py")


def test_clean_run_is_compacted_but_keeps_summary():
    out = filter_pytest("pytest -q", CLEAN, 0)
    assert out is not None
    assert "42 passed in 1.23s" in out          # summary preserved
    assert len(out) < len(CLEAN)                 # noise collapsed
    assert "tests/test_b.py ...................." not in out  # per-file dots dropped


def test_failing_run_preserves_failure_verbatim():
    # Important #1: filter returns None on failure; dispatcher ensures content survives.
    assert filter_pytest("pytest -q", FAILING, 1) is None
    # Content survival proven at the dispatcher level:
    dispatched = filter_output("pytest -q", FAILING, 1)
    assert "assert 1 == 2" in dispatched
    assert "tests/test_x.py:7: AssertionError" in dispatched
    assert "1 failed, 2 passed" in dispatched


# Minor #4: unrecognized shape (rc=0, no summary line) → None (decline)
def test_unrecognized_shape_returns_none():
    out = filter_pytest("pytest -q", "some output with no summary line\n", 0)
    assert out is None


# Minor #6: rc=0 but FAILURES section present → filter declines, full output survives
ZERO_RC_WITH_FAILURES = (
    "============================= test session starts =============================\n"
    "collected 1 item\n\n"
    "=================================== FAILURES ===================================\n"
    "___________________________________ test_z ____________________________________\n"
    "    def test_z():\n"
    ">       assert False\n"
    "E       AssertionError\n\n"
    "tests/test_z.py:2: AssertionError\n"
    "============================== 1 failed in 0.01s ==============================\n"
)


def test_content_driven_failure_detection():
    """rc=0 but FAILURES section present → filter returns None, dispatcher passes full output."""
    assert filter_pytest("pytest -q", ZERO_RC_WITH_FAILURES, 0) is None
    dispatched = filter_output("pytest -q", ZERO_RC_WITH_FAILURES, 0)
    assert "AssertionError" in dispatched
    assert "FAILURES" in dispatched
