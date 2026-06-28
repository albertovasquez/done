"""Guards the worktree test-import-resolution contract (conftest.py).

`done` mandates worktree development (AGENTS.md #1) but documents one repo-root
editable install, whose absolute-path finder otherwise pins every worktree's tests
to the root checkout. tests/conftest.py must make `import harness` resolve to the
worktree the tests live in — independent of cwd and of the editable finder.

This test deliberately does NOT add any sys.path entry of its own; it relies solely
on conftest having run.
"""
from pathlib import Path

import harness

_TEST_ROOT = Path(__file__).resolve().parent.parent


def test_harness_resolves_under_this_worktree():
    """`import harness` must resolve to THIS test tree's worktree, not whatever
    absolute path the editable finder was pinned to at install time."""
    resolved = Path(harness.__file__).resolve()
    assert resolved.is_relative_to(_TEST_ROOT), (
        f"harness imported from {resolved}, expected under {_TEST_ROOT}. "
        "The editable finder shadowed the worktree source — conftest.py must "
        "prepend the worktree root to sys.path."
    )
