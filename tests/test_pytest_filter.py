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
    assert not matches("git status")


def test_clean_run_is_compacted_but_keeps_summary():
    out = filter_pytest("pytest -q", CLEAN, 0)
    assert out is not None
    assert "42 passed in 1.23s" in out          # summary preserved
    assert len(out) < len(CLEAN)                 # noise collapsed
    assert "tests/test_b.py ...................." not in out  # per-file dots dropped


def test_failing_run_preserves_failure_verbatim():
    out = filter_pytest("pytest -q", FAILING, 1)
    # The FAILURES block and the assertion MUST survive untouched.
    assert "assert 1 == 2" in out
    assert "tests/test_x.py:7: AssertionError" in out
    assert "1 failed, 2 passed" in out
